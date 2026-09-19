# Audit — SD card write load: RedisBuffer, data partition format, and the 2014 investigation

**Audit date:** 2026-08-27
**Scope:** `RedisBuffer` feed engine, emonSD data partition format (`ext2 -b 1024`), the 2014 [docs/timeseries/Write-load-investigation.md](../docs/timeseries/Write-load-investigation.md), and the endurance of the shipped card (`SDSDQAF3-016G-I`)
**Status:** Analysis and proposal — **no measurement taken on real hardware**; see Provenance below

---

## Provenance — read this first

This document mixes three kinds of claim. They deserve different levels of trust:

| Marked | Meaning |
|---|---|
| **[measured]** | Actually run and observed. Filesystem structure figures were produced with `mkfs`/`dumpe2fs` on a dev machine. |
| **[modelled]** | Produced by a validated script fed **synthetic** traces. The arithmetic is right; the inputs are constructed, not captured. |
| **[derived]** | Reasoned from documented kernel/flash behaviour. Sound, but not observed. |

**No measurement in this document was taken on a real emonPi or a real SD card.** Section 6 sets out the hardware measurements needed to confirm or refute it. Nothing here should be treated as field data.

---

## 1. Summary

emoncms carries three layered mitigations against SD card wear:

1. **RedisBuffer** — a feed engine that buffers writes in Redis and flushes to PHPFina every 60s via a `feedwriter` daemon
2. **ext2** for the data partition — chosen partly to avoid journal writes
3. **`-b 1024`** block size — chosen to make each write smaller

The conclusions of this review:

- **The wear problem was real in 2014 and these were rational responses to it** — cards were failing in the field, and deferring writes is a sound way to reduce flash wear.
- **Two of the three mitigations do not do what they were intended to do.** They optimise bytes submitted to the block layer, which is not what wears out flash.
- **RedisBuffer buys roughly 2×.** A single sysctl (`vm.dirty_expire_centisecs`) buys ~20×, system-wide, with no code.
- **The shipped industrial card is MLC at 3K cycles, not SLC.** It improves raw endurance ~3× over consumer TLC — helpful, but it does not retire the problem. Its power-immunity and ECC features may matter more than its cycle count.
- **Feed writes are probably not the binding constraint** at typical feed counts, which specifically undercuts the rationale for RedisBuffer: it optimises the component with the most headroom, and does nothing for MySQL, logs or Redis persistence.
- **Power-loss corruption is the more likely failure mode, and all three mitigations make it worse.**

**Headline recommendation:** simplify. Remove RedisBuffer, move to ext4 with a long commit interval, and tune kernel writeback instead. This both reduces wear more than the current stack does and materially improves power-loss resilience.

**Before acting, run the §6.4 capture.** It costs an afternoon and would establish what fraction of writes are actually feed data — which determines whether any of this is worth doing.

---

## 2. The measurement problem

Everything downstream follows from this, so it is worth stating precisely.

`/proc/diskstats` sectors-written and `iostat kB_wrtn/s` measure **what the kernel submits to the block device**. Flash wear is caused by **NAND page programs**, which occur at page granularity — typically 8–16 KiB — and are remapped by the card's FTL at allocation-unit granularity, typically 4 MiB.

A 512-byte write and a 4096-byte write to the same region cost **the same single page program**.

Therefore: [derived]

- Optimisations that **defer** writes reduce the *number* of page programs. These work.
- Optimisations that **shrink** writes below the page size reduce submitted bytes but not page programs. These mostly do not work.
- Optimisations that shrink writes at the cost of **more frequent metadata updates** can be net negative.

The 2014 investigation measured submitted bytes throughout. Its conclusions about deferral are sound. Its conclusions about filesystem choice and block size are artefacts of the metric.

---

## 3. Findings

### 3.1 RedisBuffer

**What it buys.** PHPFina never calls `fsync()` — `post_multiple()` in [Modules/feed/engine/PHPFina.php](../Modules/feed/engine/PHPFina.php) does `fopen`/`fseek`/`fwrite`/`fclose` and nothing more. Writes land in the page cache and the kernel decides when they reach the card. **The kernel is already doing what RedisBuffer was built to do.** [measured — confirmed by grep across the engine]

RedisBuffer therefore does not convert small writes into batched writes. It only shifts *when* writeback fires: from every ~30s (`vm.dirty_expire_centisecs` default) to every 60s (`redisbuffer.sleep`).

The data partition is ext2, which has no journal and for which `commit=` is not a valid mount option. So nothing filesystem-side forces earlier writeback either. [derived]

Page programs per hour, 30 feeds at 10s interval: [modelled]

| configuration | page programs/hour | relative |
|---|---|---|
| no buffer, writeback @30s | 3,600 | 1.0× |
| RedisBuffer `sleep=60` | 1,800 | 2.0× |
| `vm.dirty_expire_centisecs=60000` | 180 | **20×** |

**What it costs.** All of the following are in the current code:

- **~5 Redis round-trips per datapoint** — `RedisBuffer::post()` issues a lock `hSet`, a lock `hGet`, `zRemRangeByScore`, `zAdd`, `sAdd` and an unlock `hSet`, replacing one buffered `fwrite`.
- **A blocking `sleep(1)` loop in the input API request path** — `checkLock_blocking()`. A wedged `feedwriter` stalls incoming posts indefinitely.
- **The lock protocol is not crash-safe.** [scripts/feedwriter.php](../scripts/feedwriter.php) clears stale write locks at startup, which is an admission that it leaks them.
- **User-visible data inconsistency.** The buffer merge in [Modules/feed/feed_model.php](../Modules/feed/feed_model.php) is gated on `!$average && is_numeric($interval) && $csv==false`, so the most recent 60s of data silently disappears from averaged queries and CSV exports. **This is a live bug, and the buffer is enabled by default on emonPi.**
- **Durability.** Up to 60s × every feed held in RAM. If Redis persistence is enabled to mitigate that, the avoided writes return to the same card with a larger payload.
- **Engine surface.** Every method needs a shadow implementation: synthetic `get_meta`, `get_feed_size` returning a count rather than a size, `export`/`trim`/`clear` unsupported, plus buffer-aware branches at three separate delete paths.

**Assessment.** A 2× wear reduction does not justify this. The same benefit and more is available from one sysctl.

### 3.2 ext2 and `-b 1024`

The install guide's stated rationale is that a smaller block size gives *"significant write load reduction"* for small frequent writes across many files.

**The first half is correct.** For block size < page size, ext2 tracks dirtiness per buffer_head within each 4 KiB page, so only the dirty 1 KiB buffers are submitted. That is a real 4× reduction in bytes reaching the block layer. [derived]

**The card then discards the benefit.** Both a 1 KiB and a 4 KiB write to the same region cost one page program: [modelled]

| write size | bytes submitted | distinct pages touched |
|---|---|---|
| 1 KiB (`ext2 -b 1024`) | 1.053 kB/s | **150** |
| 4 KiB (`ext4 -b 4096`) | 4.211 kB/s | **150** |

A 4× difference on the old metric; zero difference in page programs.

**And there is a real cost on the other side.** Filesystem structure at the emonSD data partition size (25 GB): [measured]

| | block groups | max file size |
|---|---|---|
| ext2, 1024 b | **3,200** | 16 GiB |
| ext2, 4096 b | 200 | 2 TiB |
| ext4, 4096 b | 201 | 16 TiB |

16× more block groups means 16× more metadata regions to dirty. Worse, ext2 has no extents. With 1 KiB blocks and 256 pointers per block, a file needs an indirect block past 12 KiB and a double-indirect past 268 KiB. A feed at 10s interval grows ~12.6 MB/year, so every feed file more than a few days old is permanently in double-indirect territory. [derived]

Per feed, a 1 KiB block holds 256 datapoints ≈ **43 minutes** at 10s interval, so a new block — plus bitmap, inode and indirect updates — is allocated every 43 minutes. At 4 KiB it is every ~2.8 hours. With ext4 extents, a contiguously appended file needs essentially zero indirect metadata. **`-b 1024` makes scattered metadata writes 4× more frequent, and scattered metadata writes are the most wear-expensive pattern there is.**

**On the journal.** *"Journalling records all disk writes to a journal first"* describes `data=journal`, which is not the default. The default is `data=ordered` — only metadata goes through the journal; data blocks are written directly to their final location. Journalling does not double data writes. Journal writes are also sequential appends to a contiguous region, which is the friendliest possible pattern for an FTL, whereas ext2's scattered metadata updates are random. **Trading sequential writes for random ones to save bytes is a bad exchange in wear terms.** [derived]

If a journal is genuinely unwanted, `mkfs.ext4 -O ^has_journal` gives extents, delayed allocation and a better allocator with no journal — strictly better than ext2 for this workload.

**On the MySQL rationale.** The guide states ext2 was chosen because it *"supports multiple linux user ownership options"* needed for MySQL data folders. This is not correct — ext4 has identical ownership, permission and ACL semantics. There is no ownership capability ext2 has that ext4 lacks. This is most likely a garbled recollection of the earlier **FAT** proposal, which genuinely cannot express Unix ownership; the rationale appears to have been re-attached to ext2-vs-ext4, where it does not apply.

Separately, InnoDB's page size is 16 KiB and it `fsync()`s. On a 1 KiB filesystem each InnoDB page spans 16 blocks with indirect-block lookups behind them, and the fsync prevents any coalescing. If MySQL data lives on this partition, `-b 1024` costs there too.

### 3.3 Corrections to [docs/timeseries/Write-load-investigation.md](../docs/timeseries/Write-load-investigation.md)

**What holds up:**

- **Commit interval dominates everything else** (Tests 1→5, ~50× from 1s → 30min). Correct, and correct at the NAND level too, because deferral genuinely reduces page program count. This is the one lever in the document that survives intact.
- **PHPFiwa → PHPFina and dropping the npoints metafile.** Fewer files means fewer distinct pages touched per interval. A genuine wear win, correctly identified, and shipped.
- **MySQL feeds are disproportionately expensive.** Correct. The index-maintenance hypothesis is right; the deeper reason, not mentioned, is that InnoDB `fsync()`s on commit so none of it can be deferred.
- **The 8× block-size explanation.** Arithmetic checks out: ~9,370 bytes/datapoint ≈ 2.3 × 4 KiB ≈ data block + inode table block + journal, against vFAT's ~1024 ≈ 2 × 512. Same structure, different block size. Sound as far as it goes.

**What is wrong:**

1. **The metric cannot see the layer that wears out** (section 2 above). This invalidates the filesystem ranking and the block-size conclusion — including the `-b 1024` result that was carried into the install guide.

2. **The inode cost model is over-counted.** The document treats each file as carrying a full-block inode write (*"a further 512 bytes"*, *"4096 for the file descriptor"*). Inodes are 128–256 bytes and are packed into shared inode-table blocks — 16 per 4 KiB block at 256 bytes. Many feeds' inodes share a block and are written back once. Per-file inode cost is amortised, not additive. This inflates the apparent benefit of reducing file count. (Also, *"file descriptor"* should read *"inode"*.)

3. **The journalling mechanism is described wrongly** — `data=journal` rather than the default `data=ordered`, as above.

4. **The document contradicts its own control experiment.** The note *"Testing on Ext2 which is a non-journaling file system show the same results as obtained on Ext4"* is a well-designed control that directly refutes the conclusion that journalling is a major write source. This was half-noticed (*"journaling on Ext4 isn't as large a source of write load initially thought"*) but the conclusion section was never updated — and the "journalling is expensive" framing is what carried forward into the ext2 choice. **The ext2 decision rests on a claim the document's own data had already falsified.**

5. **The FAT recommendation aged worst.** Beyond the metric problem, FAT concentrates its allocation table in one small region rewritten on every allocation. On cards with weak or dynamic-only wear levelling this burns a small set of erase blocks. FAT scored best on the metric used and is plausibly the worst real choice — the clearest illustration of the measurement problem. It also has no ownership or permissions, which is very likely the origin of the confused rationale in the install guide.

**The best line in the document was never followed up:**

> *"It would be interesting to compare the performance of the FAT filesystem + 5 min application based commit time with the EXT4 filesystem with journalling turned off and filesystem delayed allocation set to 5 min instead of write buffering in the application."*

That is exactly the right experiment. It was identified in 2014 and appears never to have been run. Had it been, it would likely have shown that kernel writeback tuning achieves the application buffer's result without the application buffer — and RedisBuffer might never have been built.

The framing gap that made it a footnote: deferred writeback was treated as an *ext4 delayed-allocation feature* rather than as generic page-cache behaviour governed by `vm.dirty_expire_centisecs`. That sysctl appears nowhere in the document. That omission is what steered the answer toward application-level buffering.

---

## 4. What changed: industrial cards

### Correction: the shipped card is MLC, not SLC

The community discussion on this was framed around *industrial SLC NAND*, but the part actually shipped is **SanDisk Industrial microSDHC 16GB, `SDSDQAF3-016G-I`**. The SanDisk product brief states **MLC** NAND, **3K** endurance (3,000 P/E cycles), -25°C to 85°C. [measured — vendor datasheet]

The datasheet's own headline confirms the arithmetic: *"Up to 384 Terabytes Written"* is exactly 128 GB × 3,000. **TBW is quoted as capacity × cycles with no derating for FTL write amplification** — it is a raw NAND programming budget, not a host-write budget.

For the 16 GB part: **16 GB × 3,000 = 48 TB.**

> An earlier draft of this document assumed SLC at ~60,000 cycles and put card life in the hundreds of years. That was wrong by roughly 20×. The corrected picture is below, and it changes the emphasis — though not, as it turns out, the recommendations.

### What the budget actually buys

Feed writes alone, derived from the page-program rates in §3.1 at 16 KiB pages: [modelled]

| configuration | per feed @10s | 30 feeds | 100 feeds | 300 feeds |
|---|---|---|---|---|
| no buffer, writeback @30s | 17.2 GB/yr | 517 GB/yr | 1.72 TB/yr | 5.17 TB/yr |
| RedisBuffer `sleep=60` | 8.6 GB/yr | 258 GB/yr | 0.86 TB/yr | 2.58 TB/yr |
| `dirty_expire_centisecs=60000` | 0.84 GB/yr | 26 GB/yr | 84 GB/yr | 0.26 TB/yr |

Card life against the 48 TB budget, **from feed writes alone**:

| feeds | no buffer | RedisBuffer | sysctl |
|---|---|---|---|
| 30 | 93 yr | 186 yr | >1,800 yr |
| 100 | 28 yr | 56 yr | 557 yr |
| 300 | **9 yr** | 19 yr | 186 yr |

### Three conclusions

**1. The hardware change helped, but far less than "SLC" implied.** MLC at 3K against consumer TLC at roughly 0.5–1.5K is about a 3× improvement in raw cycles — not the 20–60× that SLC would have given. The larger practical gain is probably the non-cycle features the brief advertises: wear levelling, **power immunity**, ECC and Dynamic Bit Flip Protection. Those address the corruption failure mode discussed below, which may matter more than the cycle count.

**2. Feed writes are probably not the binding constraint.** At typical feed counts they consume a few percent of the budget even with no buffering at all. **This is the strongest argument yet for removing RedisBuffer:** it optimises the one component that already has decades of headroom, while doing nothing for MySQL, logging or Redis persistence — which are unmeasured and may well dominate the real write load.

**3. Feed count is the swing factor.** At 300 feeds with no deferral the margin narrows to single-digit years, and installs that size exist. This is precisely why the sysctl change matters: it is the one intervention that holds up across the whole range, and it covers the non-feed writers too.

### An implication for the historical failures

At 30 feeds, even a consumer 16 GB TLC card at ~1K cycles yields ~30 years from feed writes alone. If emonSD cards were failing in 1–3 years in the field, **feed writes were probably not the primary cause.** MySQL, logging, Redis persistence, or power-loss corruption misdiagnosed as wear are better candidates.

That is a hypothesis, not a finding. The blktrace capture in §6.4 would settle it directly by showing what fraction of block-layer writes are actually feed data. It is worth doing before investing further effort anywhere in this area — including in the recommendations below.

### The failure mode question

With a 48 TB budget the wear margin is comfortable but not unlimited, so this is a trade rather than a rout. Still, the dominant risk on a mains-powered, headless device is power-loss corruption, and three of our mitigations work against it:

- **ext2** — no journal, full `e2fsck` on every unclean shutdown, systemd emergency mode if it needs a human. On a headless device that presents as a brick.
- **RedisBuffer** — up to 60s × every feed held in volatile memory.
- **PHPFina** — no `fsync` anywhere, so a further ~30s in page cache.

All three were wear optimisations. Together they form close to the worst possible power-cut posture. Given that feed writes appear not to be the binding wear constraint (conclusion 2), trading most of this back for durability looks like a clear win — and it complements the card's own power-immunity firmware rather than fighting it.

**One caveat.** Many users flash emonSD onto whatever card they have, so image defaults must remain sane on consumer media. This creates no conflict: `vm.dirty_expire_centisecs` protects those users considerably better than RedisBuffer does, and the ext4 change benefits everyone regardless of card.

---

## 5. Recommendations

In priority order.

**1. Fix the averaged/CSV data gap.** Independent of everything else. The buffer is on by default on emonPi and the last 60s is silently missing from averaged queries and CSV exports. This is a live correctness bug.

**2. Set `errors=remount-ro` on the data partition.** Cheap, immediate, independent of the ext2/ext4 decision:
```
tune2fs -e remount-ro /dev/mmcblk0p3
```
Continuing to write to a filesystem that has already reported corruption turns a recoverable fault into an unrecoverable one.

**3. Replace RedisBuffer with kernel writeback tuning.** More reduction, no daemon, no locks, no read-path merge, no data gap.

`vm.dirty_expire_centisecs` sets how long a dirty page may sit in the page cache before writeback may flush it; `vm.dirty_writeback_centisecs` sets how often the flusher threads wake to look. Units are centiseconds (1/100 s).

| | default | proposed |
|---|---|---|
| `vm.dirty_expire_centisecs` | 3000 (30s) | 60000 (600s) |
| `vm.dirty_writeback_centisecs` | 500 (5s) | 60000 (600s) |

Test live:
```
sudo sysctl -w vm.dirty_expire_centisecs=60000 vm.dirty_writeback_centisecs=60000
```
Persist:
```
echo -e "vm.dirty_expire_centisecs = 60000\nvm.dirty_writeback_centisecs = 60000" \
  | sudo tee /etc/sysctl.d/60-emoncms-writeback.conf
```

This works because PHPFina never calls `fsync()` — the kernel is already free to defer, and this tells it to defer for 10 minutes rather than 30 seconds. It applies to every writer on the system, so unlike RedisBuffer it also covers MySQL's non-synchronous writes, logging and Redis persistence.

Two caveats: `dirty_background_ratio` (10% of RAM) still forces earlier writeback under memory pressure; and the exposure on power cut goes from 60s to 10 minutes — the same class of risk RedisBuffer already carries, which is why it should be paired with recommendation 4 rather than adopted on ext2.

**4. Move the data partition to ext4.** For the journal, extents, and the per-filesystem `commit=` lever that ext2 does not offer at any price:
```
/dev/mmcblk0p3  /var/opt/emoncms  ext4  noatime,commit=600,errors=remount-ro  0  2
```

> ⚠️ **`commit=600` is not optional.** `mkfs.ext4` plus `defaults` gives `data=ordered,commit=5`, which forces data blocks out every 5 seconds for appending files — a ~6× **regression** against the current ext2 30s writeback. A naive ext2 → ext4 swap makes things worse, not better.

**5. Drop `-b 1024`.** Use the 4096 default. Fewer block groups, extents instead of indirect blocks, and better alignment with InnoDB's 16 KiB pages.

**6. Minor:** `nodiratime` is redundant — `noatime` already implies it.

### Verify before any of this

The fstab mounts `/var/opt/emoncms`, but committed settings still point at `/home/pi/data/phpfina/` ([default.emonpi.settings.php](../default.emonpi.settings.php)). Presumably a symlink or image-level override, but confirm the feed data actually lands on p3:
```
df /home/pi/data/phpfina && readlink -f /home/pi/data
```
If it resolves to the root partition on any shipped image, the feed writes are not on the tuned partition at all and this entire analysis is aimed at the wrong device.

---

## 6. How to measure and prove it

### 6.1 Read the card's allocation unit — 10 seconds

The kernel parses the SD Status Register's AU_SIZE and exposes it:
```
cat /sys/block/mmcblk0/device/preferred_erase_size   # typically 4194304 (4 MiB)
cat /sys/block/mmcblk0/device/erase_size
```
If this reads 4 MB on shipped cards, the FTL remaps at 4 MB granularity and the case for 1024-byte blocks is dead on arrival. **Highest value per unit effort in this document.**

### 6.2 Direct wear telemetry

- **eMMC (CM4/CM5):** `/sys/block/mmcblk0/device/life_time` and `pre_eol_info`. `life_time` is two bytes in 10% increments (`0x01` = 0–10% consumed, `0x0A` = 90–100%). This is ground truth.
- **Industrial microSD — including the card we ship.** The SanDisk product brief for `SDSDQAF3-016G-I` explicitly advertises a **"health status meter"**, listed under both features and business benefits (*"Maximize system availability with health status meter, ensuring timely preventative maintenance"*). [measured — vendor datasheet] So wear telemetry exists on the exact part in the field. The open-source tool **`sdmon`** reads SMART-style health over CMD56 from WD/SanDisk industrial cards among others — typically remaining life, spare block count and P/E counts. Verify it against this SKU; if it works, wear stops being modelled and becomes measured.
- **Consumer microSD:** nothing. No standardised health reporting exists.

**If `sdmon` works on the cards now shipped, it converts the planned 5–10 year endurance test into a two-week measurement:** read the P/E counter, run a known workload, read it again, extrapolate. That also allows buffer-on vs buffer-off and ext2 vs ext4 to be compared in units of actual card life rather than proxies.

### 6.3 Prove the round-up with fio

```
for bs in 512 1k 2k 4k 8k 16k 64k; do
  echo -n "$bs: "
  fio --name=p --filename=/var/opt/emoncms/fio.probe --rw=randwrite --bs=$bs \
      --size=64M --runtime=20 --time_based --direct=1 --minimal 2>/dev/null | cut -d';' -f8
done
```
Flat IOPS from 512 B to 8–16 KiB means every write in that range costs one page program — the card is rounding up and shrinking writes buys nothing.

> ⚠️ Use a **file**, never the raw device — `--filename=/dev/mmcblk0` destroys the card. This test also burns real write cycles; run it on a scratch card.

### 6.4 Count page programs, not bytes

`blktrace` captures every BIO with LBA and length. Bucket those into page-sized units and count distinct units touched per flush cycle:
```
sudo apt install blktrace
sudo blktrace -d /dev/mmcblk0 -a write -w 3600 -o /run/trace
blkparse -i /run/trace | python3 sdwear.py \
    --au $(cat /sys/block/mmcblk0/device/preferred_erase_size) --bucket 5
```
`sdwear.py` accompanies this document. It reports submitted bytes (the old metric) alongside distinct pages and allocation units touched, and the ratio between them.

**Implementation note:** the `--bucket` flag is essential. A PHPFina tail block holds ~2.8 hours of data at 10s interval, so successive flushes rewrite the *same* page. Counting unique pages globally reports the same figure for every configuration and is useless. Bucketing by time separates each flush into its own program event. Any bucket smaller than the flush interval gives the same answer (verified at 5s, 30s, 60s and 600s).

### 6.5 The protocol

1. `cat preferred_erase_size` on three or four shipped cards. Probably settles the block-size question immediately.
2. One hour of `blktrace` on a representative emonPi: buffer on, buffer off, and with `dirty_expire_centisecs` raised. Three numbers, one afternoon.
3. If `sdmon` supports the shipped SKU, start logging P/E counters now — it is the only true wear number available, and it takes time to become useful.

**Limitation.** On consumer SD, 6.3 and 6.4 measure what the kernel sends the card, bucketed into a model of what the card does with it. That is a substantial improvement on summed bytes and it makes the right distinctions, but it remains a model. 6.3 validates the model's central assumption and 6.1 validates its granularity parameter. Only eMMC `life_time` or industrial card health data is ground truth.

---

## 7. Open questions

1. Do shipped industrial cards expose health via `sdmon`? This determines whether wear becomes measurable or stays modelled.
2. Does feed data actually land on p3 across all shipped images? (§5, *Verify before any of this*.)
3. Is Redis persistence enabled on emonSD? If so, RedisBuffer's avoided writes partly return via RDB/AOF dumps.
4. **What fraction of block-layer writes are actually feed data?** (§6.4.) This is the highest-value open question: if feeds are a small minority, the entire RedisBuffer/PHPFina optimisation effort has been aimed at the wrong component for a decade.
5. Does `sdmon` read the health status meter on `SDSDQAF3-016G-I`? The datasheet says the meter exists; whether it is reachable with open tooling is unconfirmed.
6. What is the endurance rating of the cards used before the industrial switch, and did observed failure rates actually change afterwards? This would validate or refute the §4 hypothesis about historical failures.
