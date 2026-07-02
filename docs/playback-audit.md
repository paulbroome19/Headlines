# Playback Experience Audit — Headlines vs. a Professional Audio Player

**Scope:** the full live playback system — `BulletinPlayer.swift`, `NowPlayingView.swift`,
the `MPRemoteCommandCenter` wiring, `MPNowPlayingInfoCenter` info, the scrubber/timeline,
and the `AVQueuePlayer` queue. **Read-only.** Audited against `main` @ `0fcd13a` (includes
#99/#100/#101/#102/#103/#104).

**Frame.** A world-class audio player (Apple Music / Spotify / Overcast / Apple Podcasts)
has three non-negotiables: (1) **one playback state**, reflected identically on every
surface at every moment; (2) **one continuous timeline** — elapsed/remaining and a
scrubbable bar that run from the first second to the last, the same in-app and on the lock
screen; (3) **every control does the same thing from every surface**, and the system
(interruptions, route changes, lock screen) behaves the way the user's muscle memory
expects. None of this is novel — it's the baseline. Anywhere we deviate is a finding.

**Headline result.** The *play/pause state machine*, *interruption handling*, and
*remote-command unification* are now genuinely solid — real-player grade. The biggest
remaining gaps are all on the **timeline**: the **lock screen shows a per-segment countdown
that resets ~20 times per briefing** and disagrees with the in-app full-timeline; the
**lock-screen bar can't be scrubbed**; the **scrubber has no live drag feedback**; and the
**play button is dead at end-of-briefing**. Details below.

Legend: ✅ correct · 🟠 works-but-fragile / deviates · 🔴 broken-or-deviates-materially

---

## 1. State & Controls

### 1.1 Play/pause — one source of truth, icon matches audio everywhere ✅ (strong)
- **Professional:** one boolean truth; in-app, lock screen, Control Center, CarPlay, headset
  all show and drive the same state, at first load, after skip/seek/interruption.
- **Ours:** `isPlaying` is the single definition (`BulletinPlayer.swift:132`), derived from
  `playerState`. The in-app icon reads it (`NowPlayingView.swift:450`); the lock screen's
  `MPNowPlayingInfoPropertyPlaybackRate` and `MPNowPlayingInfoCenter.playbackState` derive
  from the same `isPlaying` (`BulletinPlayer.swift:1140,1150`). First autoplay sets
  `.playing` directly so PAUSE shows immediately (`:366-373`). Remote commands now run the
  real `play()/pause()` **synchronously** on the main actor via `performRemote`
  (`:579-599`), so the lock screen and app can't diverge. **This is correct and robust.**
- 🟠 **Exception — `.ended` shows a play icon that does nothing** (see 4.4). `isPlaying` is
  false at `.ended`, so the button shows PLAY, but `play()` only acts on
  `.buffering/.paused/.stalled` (`:405-415`) → tapping it is a no-op.

### 1.2 Next / Previous — convention + rapid taps ✅
- **Professional:** single tap always registers; Previous = restart-current-then-previous
  (the ≈2s rule); rapid taps never drop or double-fire.
- **Ours:** `skip()` = next story (`:438-443`); `skipBack()` = restart current, or previous
  unit if `< 2s` in or double-tapped within 2s (`:1008-1019`) — the standard podcast
  convention, correctly implemented. Rapid taps are serialized by `navGeneration`
  (`:879-880`, checked at `:928`) so a superseded seek's completion no-ops. ✅
- 🟠 **Every next/prev is a full queue rebuild, not a gapless advance.** `skip`/`skipBack`
  route through `seekToStoryUnit`, which does `removeAllItems()` + re-enqueue + re-`seek`
  (`:914-922`). A mature player advances to an already-buffered next item instantly; ours
  tears the queue down and refetches, so a tap can incur a buffering beat. Functionally
  correct, not gapless.

### 1.3 Same action from every surface ✅ (one gap)
- In-app and lock-screen play/pause/next/prev call the **same** methods
  (`:452,432,434` in-app; `:579-583` remote). ✅
- 🟠 **Seek is the exception:** in-app scrubbing calls `seekToFullFraction`
  (`NowPlayingView.swift:412`), but the lock-screen scrub command is **disabled**
  (`changePlaybackPositionCommand.isEnabled = false`, `BulletinPlayer.swift:569`) — so that
  one control does *not* exist on the lock screen (see 3.1).

---

## 2. Timeline & Scrubber

### 2.1 Elapsed/remaining + bar from the first second (in-app) ✅
- **Professional:** time and bar advance continuously from 0:00 of the first audio
  (including intro), smoothly, at real rate.
- **Ours (in-app):** a full-audio timeline was added — `playbackElapsedSeconds` (segment
  cumulative start + position, `:811-819`) over `totalDurationSeconds` (sum of *all*
  segments, `:249-256`). The timer (`NowPlayingView.swift:583-586`) and scrubber
  (`:407-413`) use it, so elapsed counts from the greeting's first second through
  intro→stories→outro. ✅
- 🟠 **10 fps stepping, no interpolation.** The bar/timer update only when
  `playbackElapsedSeconds` republishes, i.e. every 0.1s (periodic observer,
  `BulletinPlayer.swift:623`). No `.animation`/`CADisplayLink` interpolation
  (`NowPlayingView.swift:645-651`), so the fill steps rather than glides. Acceptable, not
  silky.

### 2.2 Scrubbing — drag to seek, live time, resume state 🟠
- **Professional:** dragging shows a thumb + live time that tracks the finger; on release
  it seeks the actual audio and restores the prior play/pause state. Generous touch target.
- **Ours:** `SegmentedScrubber` uses `DragGesture(minimumDistance: 0).onEnded`
  (`NowPlayingView.swift:626-632`) — it seeks **only on release**. There is **no
  `.onChanged`**, so during the drag the time label and bar don't move, there's **no scrub
  preview and no thumb** to grab. The hit area is the bar itself at **6 pt tall**
  (`:414`) — well under the 44 pt touch guideline — with the gesture on a 6 pt-tall
  `GeometryReader` (`:608,624-632`), so a slightly-vertical drag can miss/cancel.
- ✅ On release it does seek the real audio and restores play/pause via
  `resumeAfterSeekIfIntended` (`BulletinPlayer.swift:944-949`).
- 🟠 **Can't scrub into the intro/outro:** a tap in a wrapper region maps to the nearest
  *story* unit (`:980-984`), so you can't scrub back into the greeting even though it's
  drawn on the bar.

### 2.3 Total duration + per-item vs whole ✅ (in-app)
- Total = Σ segment `durationSeconds` from the manifest (`:252-256`), which are the real
  backend audio durations. "X PLAYED / Y LEFT" is whole-briefing (`NowPlayingView:583-586`);
  the board header shows story N/total (`:552-558`). Coherent in-app. ✅ (accuracy is
  only as good as the manifest `duration_ms`.)

---

## 3. Lock Screen / Control Center / System

### 3.1 Controls present, enabled, functional 🟠
- play / pause / togglePlayPause / nextTrack / previousTrack are all enabled and wired
  (`:564-568,579-583`). ✅
- 🔴/🟠 **Scrubbing is disabled on the lock screen.** `changePlaybackPositionCommand.isEnabled
  = false` (`:569`), so the lock-screen/CarPlay progress bar is **not draggable**. Apple
  Podcasts / Music / Overcast all let you scrub from the lock screen; we don't.

### 3.2 Now-playing info accuracy 🔴 (the timeline is per-segment)
- ✅ Title = current story's headline, subtitle = ranked sources, artwork = real app icon,
  chapter N/total for stories (`:1129-1148`). These are correct and update per segment.
- 🔴 **Elapsed & duration are PER-SEGMENT, not the whole briefing.** `updateNowPlaying`
  sets `MPNowPlayingInfoPropertyElapsedPlaybackTime = positionSeconds` and
  `MPMediaItemPropertyPlaybackDuration = seg.durationSeconds` (`:1135-1136`), and the
  periodic tick keeps pushing the per-segment `secs` (`:828-830`). So the lock-screen
  progress bar **fills 0→segment-length then snaps back to 0 at every segment boundary** —
  intro, *every* transition (~2-3s each), *every* story — i.e. it resets on the order of
  20+ times in a 10-story briefing. Meanwhile the in-app bar shows one continuous
  full-briefing timeline (§2.1). **The two surfaces show fundamentally different timelines.**
  A professional player shows one continuous elapsed/duration (optionally with chapter
  markers), never a per-segment countdown that resets on the bridges.

### 3.3 Lock → app parity ✅ (state) / 🔴 (timeline)
- Play/pause/next/prev drive the same methods synchronously (`:592`) → after acting on the
  lock screen and returning to the app, the play/pause **state** matches. ✅ (this was the
  #103 fix and it holds.)
- 🔴 The **timeline** does not reach parity: lock screen is per-segment, app is
  full-briefing (from §3.2). Same root cause as 3.2.

---

## 4. Interruptions & Edge Cases

### 4.1 Phone call / other audio (AVAudioSession interruption) ✅
- `.began` → pause the engine but **keep** `intendedPlaying`; `.ended` → resume only if the
  system says `shouldResume` **and** the user still intends to play (a manual pause during
  the call clears intent) (`:507-530`). This is exactly the professional behaviour. ✅

### 4.2 Route change (headphones unplugged) ✅
- `oldDeviceUnavailable` → pause and **clear** `intendedPlaying`, so it does not
  auto-resume when re-plugged (`:532-545`) — matches Apple's "pause on unplug, don't blast
  the speaker" convention. ✅

### 4.3 Backgrounding / foreground / lock ✅ (one data gap)
- The player is a single `@StateObject` on `HomeContainerView` (`HomeContainerView.swift:19`)
  and audio continues in the background; state survives foregrounding because it's the same
  instance. ✅
- 🟠 **No `scenePhase` / termination hook.** `HeadlinesApp` has no
  background/terminate handling, so `sendSummary()` (the batched listen-analytics, meant to
  fire "on background/terminate" per `BulletinPlayer.swift:68`) only fires on an explicit
  Back/close (`HomeContainerView.swift:59` → `stop()` → `:646`). Backgrounding or killing
  mid-briefing drops the accumulated events. Not a control bug — a data-loss gap.

### 4.4 End of briefing 🟠
- On queue-exhaustion `playerState = .ended` and the lock screen is cleared
  (`nowPlayingInfo = nil`, `:744-761,1118-1121`). But:
  - 🟠 **The play button is dead at the end.** `play()` ignores `.ended` (`:405-415`), so
    tapping in-app PLAY or (if it were shown) lock-screen PLAY does nothing. The user can
    only replay via a tracklist row (`playStoryUnit`, `NowPlayingView.swift:517`) or by
    dragging the scrubber (`seekToFullFraction`) — both of which rebuild from a unit. A real
    player lets PLAY restart from where it makes sense (or shows a clear "replay/completed"
    affordance).
  - 🟠 There is no explicit "briefing complete" state in the UI switch — `.ended` falls into
    `default` → `playerBody` (`NowPlayingView.swift:52-54`), showing the last story with a
    (dead) play icon and a full bar.

### 4.5 Loading / buffering 🟠
- `.preparing` shows the loader with a 60s hard cap so it can't hang (`:316`), and a
  blocked-segment path fails cleanly (`:325-327`). ✅
- 🟠 **Momentary wrong icon during a seek.** `seekToStoryUnit` sets `.buffering` while
  rebuilding (`:914`); `.buffering` is not `isPlaying`, so the transport flips to the PLAY
  icon for the rebuild window even when it's about to resume, then flips back on
  `resumeAfterSeekIfIntended` (`:944-949`). A brief but visible icon flicker on every
  skip/seek while playing.

---

## 5. Queue & Transitions

### 5.1 Intro → stories → outro flow ✅ (in-app) / 🔴 (lock screen)
- `AVQueuePlayer` with a sliding window (enqueue first two, prefetch N+2 on readyToPlay,
  `:628-631,704-708`) gives near-gapless playback. In-app the timeline stays continuous
  across boundaries because `playbackElapsedSeconds` snaps to each segment's cumulative
  start on advance (`:768-770`). ✅
- 🔴 On the **lock screen** each boundary resets the progress bar to 0 (§3.2). So the
  transitions that should be invisible instead make the system UI jump ~20 times.

### 5.2 Tracklist / chapter tap ✅ (with a caveat)
- Tapping a row → `playStoryUnit` → `seekToStoryUnit` + `play` (`NowPlayingView.swift:517`,
  `BulletinPlayer.swift:400-403`), which updates `currentUnitIndex`, headline, scrubber, and
  now-playing. Played rows are dimmed but replayable; the current row shows an equaliser.
  ✅
- 🟠 Same full-rebuild cost as §1.2 on every tap.

### 5.3 Latent: second player instance can hijack the remote commands 🟠
- `setupRemoteCommands` clears with `removeTarget(nil)` then binds to `self`
  (`:549-584`); whichever `BulletinPlayer` initialised **last** owns the shared command
  centre. The live app has exactly one (`HomeContainerView.swift:19`), but
  `BriefingViewModel` still **eagerly constructs a second** `BulletinPlayer` in a stored
  property (`Features/Briefing/BriefingViewModel.swift:32`). It's currently dead code (never
  instantiated), but if anything ever builds a `BriefingViewModel`, its `init` would clobber
  the live player's handlers and reproduce the exact "lock screen controls the wrong player"
  class of bug. Latent landmine.

### 5.4 Minor: now-playing dict rewritten 10×/s 🟠
- The periodic tick reads and rewrites the **entire** `MPNowPlayingInfoCenter.nowPlayingInfo`
  dictionary every 0.1s to bump elapsed (`:828-831`). Cheaper is to keep a mutable dict and
  set only the elapsed key, or update less often; more importantly, this is where the
  per-segment `secs` (§3.2) gets pushed.

---

## Prioritised findings

### 🔴 Must-fix for a credible audio player
1. **Lock-screen timeline is per-segment and resets ~20×/briefing** — and disagrees with
   the in-app full-briefing timeline. Set `MPNowPlayingInfoPropertyElapsedPlaybackTime =
   playbackElapsedSeconds` and `MPMediaItemPropertyPlaybackDuration = totalDurationSeconds`
   (one continuous timeline), instead of the current per-segment values
   (`BulletinPlayer.swift:1135-1136,828-830`). This is the single biggest "not a real audio
   player" tell that remains. (§3.2, §3.3, §5.1)
2. **Lock-screen scrubbing is disabled** — enable `changePlaybackPositionCommand` and wire it
   to `seekToFullFraction` (needs #1 first, so the lock-screen timeline it scrubs is the
   whole briefing). (`:569`) (§3.1)

### 🟠 Works-but-fragile — fix next
3. **End-of-briefing play button is dead** — `play()` no-ops at `.ended`; give a clear
   completed/replay state and make PLAY restart. (`:405-415`, §4.4)
4. **Scrubber has no live drag feedback, no thumb, 6 pt hit target** — add `.onChanged` live
   time + a thumb + a taller touch area. (`NowPlayingView.swift:626-632,414`, §2.2)
5. **Icon flicker to PLAY during seek/skip** while playing (the `.buffering` window).
   (`:914`, §4.5)
6. **Every next/prev/tracklist-tap fully rebuilds the queue** rather than advancing a
   pre-buffered item — not gapless. (`:914-922`, §1.2/§5.2)
7. **Stall-skip uses raw `advanceToNextItem`** (`:1050`), a different path from the
   index-based navigation everything else uses — can advance into a not-ready item.
8. **No background/terminate hook** → `sendSummary()` analytics lost on background/kill.
   (§4.3)
9. **Latent second-`BulletinPlayer` clobber risk** from `BriefingViewModel` (dead code).
   (§5.3)
10. **Now-playing dict rewritten 10×/s.** (§5.4)

### Polish
11. Bar/timer step at 10 fps (no interpolation). (§2.1)
12. Can't scrub into the intro/outro region. (§2.2)
13. Lock-screen (and in-app board) title shows the *first story's* headline during the
    greeting, rather than a "Headlines / Good morning" label. (`:1128-1129`)

---

## Verdict — does this feel like a real audio player?

**Mostly yes on control, not yet on the timeline.** The hard part — a single play/pause
truth reflected everywhere, correct interruption/route behaviour, and remote commands that
drive the *same* code as the buttons — is now genuinely at Apple-Podcasts standard, and the
in-app scrubber counts the whole briefing continuously. Those were the reactive-patch
areas, and they've converged into a coherent state machine.

What still breaks the illusion is the **lock screen** and the **ends of the experience**:
pick up your phone mid-briefing and the Now Playing bar is doing a stuttering per-segment
countdown that resets on every bridge, you can't drag it, and when the briefing finishes the
play button quietly stops working. A mature player's lock screen is a faithful, scrubbable
mirror of one continuous timeline, and its transport never dead-ends. Fix 🔴 #1–#2 (one
lock-screen timeline + enable scrubbing) and 🟠 #3 (end-of-briefing), and Headlines crosses
the bar from "solid transport with a giveaway lock screen" to "feels like a real audio app."
