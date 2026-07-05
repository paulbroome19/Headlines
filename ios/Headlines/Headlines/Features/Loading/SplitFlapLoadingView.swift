//
//  SplitFlapLoadingView.swift
//  Headlines
//
//  A split-flap (Solari) departure-board launch animation that resolves to
//  "HEADLINES". Every cell flickers through random glyphs, then the board
//  settles in a left-to-right ripple — the centre row landing last for the
//  "boom". Drop in as the app's loading state:
//
//      SplitFlapLoadingView { showApp = true }
//
//  Drives every cell from a single TimelineView clock (no per-cell timers);
//  cell state is a pure function of elapsed time, so it's cheap and 60fps.
//
//  Almost everything you'd want to dial in lives in `Config` at the top of
//  this file. Colours and shared material live in Shared/Theme/Board.swift.
//

import SwiftUI
import UIKit
import AVFoundation

// MARK: - Local config (timing, layout, animation — not shared)

private enum Config {
    // ── Board layout ──────────────────────────────────────────────
    static let rows = 3                 // tune: rows in the grid
    static let cols = 11                // tune: columns (wider than tall)
    static let word = "HEADLINES"       // resolves centred in the middle row

    static let widthFraction: CGFloat = 0.90   // board width as a fraction of screen
    static let cellAspect: CGFloat   = 1.5     // cell height / width
    static let gap: CGFloat          = 3       // uniform gap between cells

    // ── Timing (seconds) — the feel lives here ────────────────────
    static let flipMin = 0.058          // fastest single-flap duration
    static let flipMax = 0.100          // slowest single-flap duration
    static let minFlips = 10            // flaps per cell (clamped)
    static let maxFlips = 30

    // When each group of cells locks. Centre word lands LAST for the boom.
    static let nonCenterBase   = 0.65   // earliest non-word cells settle
    static let colRippleStep   = 0.030  // per-column left→right ripple
    static let nonCenterJitter = 0.18   // random scatter on non-word cells
    static let centerSettleStart = 1.42 // first letter (H) lands
    static let centerLetterStep  = 0.075// each successive HEADLINES letter later

    static let holdDuration = 0.50      // hold the resolved board before onComplete
    static let settleDur = 0.14         // landing settle (scale nudge) duration
    static let settleAmp: CGFloat = 0.05

    // ── Look ──────────────────────────────────────────────────────
    static let perspective: CGFloat = 0.42  // 3D foreshortening of the flaps
    static let foldSign: Double = -1        // flip to +1 if the fold looks inverted
    static let maxShade = 0.55              // how dark a flap goes edge-on

    // ── Feedback ──────────────────────────────────────────────────
    static let soundEnabledByDefault = true // faint per-settle tick (ambient, mixable)
    static let tickVolume: Float = 0.12
    static let tickVolumeAccent: Float = 0.22

    static let flickerGlyphs: [Character] =
        Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&★◆")

    // Characters route through the shared board token (DejaVu Sans Bold, bundled).
    static func font(size: CGFloat) -> Font { .board(size) }
}

// MARK: - Cell model (built once, immutable)

private struct CellModel {
    let row: Int
    let col: Int
    let target: Character?          // nil = blank flap
    let isTargetLetter: Bool        // part of "HEADLINES"
    let flipDur: Double
    let nFlips: Int
    let lockTime: Double            // when this cell stops flipping
    let seq: [Character?]           // length nFlips+1, last == target
}

private final class FlapBoardModel: ObservableObject {
    let grid: [[CellModel]]
    let cells: [CellModel]
    let boomTime: Double            // last centre-letter landing
    let totalDuration: Double       // board fully resolved + hold

    init() {
        var rng = SystemRandomNumberGenerator()
        let word = Array(Config.word)
        let centerRow = Config.rows / 2
        let startCol = max(0, (Config.cols - word.count) / 2)

        var grid: [[CellModel]] = []
        var flat: [CellModel] = []

        for r in 0..<Config.rows {
            var rowCells: [CellModel] = []
            for c in 0..<Config.cols {
                let isLetter = (r == centerRow && c >= startCol && c < startCol + word.count)
                let target: Character? = isLetter ? word[c - startCol] : nil

                let settle: Double
                if isLetter {
                    settle = Config.centerSettleStart + Double(c - startCol) * Config.centerLetterStep
                } else {
                    settle = Config.nonCenterBase
                        + Double(c) * Config.colRippleStep
                        + Double.random(in: 0...Config.nonCenterJitter, using: &rng)
                }

                let flipDur = Double.random(in: Config.flipMin...Config.flipMax, using: &rng)
                let nFlips = min(Config.maxFlips,
                                 max(Config.minFlips, Int((settle / flipDur).rounded())))
                let lockTime = Double(nFlips) * flipDur

                var seq: [Character?] = (0..<nFlips).map { _ in
                    Config.flickerGlyphs.randomElement(using: &rng)!
                }
                seq.append(target)

                let m = CellModel(row: r, col: c, target: target, isTargetLetter: isLetter,
                                  flipDur: flipDur, nFlips: nFlips, lockTime: lockTime, seq: seq)
                rowCells.append(m)
                flat.append(m)
            }
            grid.append(rowCells)
        }

        self.grid = grid
        self.cells = flat
        self.boomTime = flat.filter { $0.isTargetLetter }.map { $0.lockTime }.max() ?? 0
        self.totalDuration = (flat.map { $0.lockTime }.max() ?? 0) + Config.holdDuration
    }
}

// MARK: - Per-frame render state for a cell

private struct CellRender {
    var staticTop: Character?
    var staticBottom: Character?
    var topFlap: (glyph: Character?, angle: Double, shade: Double)?     // folding away
    var bottomFlap: (glyph: Character?, angle: Double, shade: Double)?  // falling in
    var settleScale: CGFloat = 1
}

// MARK: - The view

struct SplitFlapLoadingView: View {
    let onComplete: () -> Void

    @StateObject private var board = FlapBoardModel()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var started = false
    @State private var startDate: Date?
    @State private var audio: FlapAudio?

    init(onComplete: @escaping () -> Void) {
        self.onComplete = onComplete
    }

    var body: some View {
        ZStack {
            BoardColors.background.ignoresSafeArea()

            GeometryReader { geo in
                let cell = cellSize(for: geo.size)
                Group {
                    if reduceMotion {
                        board(cell: cell, t: .greatestFiniteMagnitude)
                    } else {
                        TimelineView(.animation) { ctx in
                            board(cell: cell, t: elapsed(ctx.date))
                        }
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
            }

            // Faint coated-material grain over the board.
            Image(uiImage: BoardGrain.image)
                .resizable(resizingMode: .tile)
                .opacity(0.012)
                .ignoresSafeArea()
                .allowsHitTesting(false)
        }
        .onAppear(perform: start)
        .onDisappear { audio?.stop() }
    }

    // MARK: Grid

    private func board(cell: CGSize, t: Double) -> some View {
        VStack(spacing: Config.gap) {
            ForEach(0..<Config.rows, id: \.self) { r in
                HStack(spacing: Config.gap) {
                    ForEach(0..<Config.cols, id: \.self) { c in
                        FlapCellView(render: render(board.grid[r][c], t: t), cell: cell)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity) // centre on screen
    }

    private func cellSize(for size: CGSize) -> CGSize {
        let totalGap = Config.gap * CGFloat(Config.cols - 1)
        let w = max(1, (size.width * Config.widthFraction - totalGap) / CGFloat(Config.cols))
        return CGSize(width: w, height: w * Config.cellAspect)
    }

    private func elapsed(_ date: Date) -> Double {
        guard let startDate else { return 0 }
        return max(0, date.timeIntervalSince(startDate))
    }

    // MARK: Pure render function (cell + time -> visual state)

    private func render(_ m: CellModel, t: Double) -> CellRender {
        if reduceMotion {
            return CellRender(staticTop: m.target, staticBottom: m.target)
        }
        if t >= m.lockTime {
            return CellRender(staticTop: m.target, staticBottom: m.target,
                              settleScale: settleScale(t - m.lockTime))
        }
        let fi = min(m.nFlips - 1, Int(t / m.flipDur))
        let from = m.seq[fi]
        let to = m.seq[fi + 1]
        let p = (t - Double(fi) * m.flipDur) / m.flipDur

        if p < 0.5 {
            // Top flap (showing the OLD top half) folds down; new top revealed behind.
            let angle = -180.0 * p                     // 0° → -90°
            return CellRender(staticTop: to, staticBottom: from,
                              topFlap: (from, angle, shade(angle)))
        } else {
            // New bottom half falls into place over the old bottom.
            let angle = 180.0 * (1 - p)                // 90° → 0°
            return CellRender(staticTop: to, staticBottom: from,
                              bottomFlap: (to, angle, shade(angle)))
        }
    }

    private func shade(_ angle: Double) -> Double {
        min(1.0, abs(angle) / 90.0) * Config.maxShade
    }

    private func settleScale(_ dt: Double) -> CGFloat {
        guard dt >= 0, dt < Config.settleDur else { return 1 }
        let k = dt / Config.settleDur
        return 1 + Config.settleAmp * CGFloat(sin(k * .pi)) * CGFloat(1 - k)
    }

    // MARK: Lifecycle / discrete events (haptics, sound, completion)

    private func start() {
        guard !started else { return }
        started = true
        startDate = Date()

        Haptics.prepareBoard()

        if reduceMotion {
            Haptics.boardSettle()
            DispatchQueue.main.asyncAfter(deadline: .now() + Config.holdDuration, execute: onComplete)
            return
        }

        if Config.soundEnabledByDefault {
            let a = FlapAudio()
            audio = a
            if a.available {
                for m in board.cells where m.lockTime > 0 {
                    let vol = m.isTargetLetter ? Config.tickVolumeAccent : Config.tickVolume
                    DispatchQueue.main.asyncAfter(deadline: .now() + m.lockTime) {
                        a.tick(volume: vol)
                    }
                }
            }
        }

        // Soft tick as the last centre flap clicks home — the word settling into place.
        DispatchQueue.main.asyncAfter(deadline: .now() + board.boomTime) {
            Haptics.boardSettle()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + board.totalDuration, execute: onComplete)
    }
}

// MARK: - Animated cell view (loader-private; uses shared FlapHalfPanel + FlapSeam)

private struct FlapCellView: View {
    let render: CellRender
    let cell: CGSize

    var body: some View {
        ZStack {
            // Static base: top and bottom halves.
            VStack(spacing: 0) {
                FlapHalfPanel(glyph: render.staticTop,    half: .top,    size: cell)
                FlapHalfPanel(glyph: render.staticBottom, half: .bottom, size: cell)
            }

            FlapSeam(size: cell)

            // Folding top flap (old top half rotating down on the hinge).
            if let tf = render.topFlap {
                FlapHalfPanel(glyph: tf.glyph, half: .top, size: cell, shade: tf.shade)
                    .rotation3DEffect(.degrees(tf.angle * Config.foldSign),
                                      axis: (x: 1, y: 0, z: 0),
                                      anchor: .bottom, perspective: Config.perspective)
                    .frame(width: cell.width, height: cell.height / 2)
                    .position(x: cell.width / 2, y: cell.height / 4)
            }

            // Incoming bottom flap (new bottom half falling into place).
            if let bf = render.bottomFlap {
                FlapHalfPanel(glyph: bf.glyph, half: .bottom, size: cell, shade: bf.shade)
                    .rotation3DEffect(.degrees(bf.angle * Config.foldSign),
                                      axis: (x: 1, y: 0, z: 0),
                                      anchor: .top, perspective: Config.perspective)
                    .frame(width: cell.width, height: cell.height / 2)
                    .position(x: cell.width / 2, y: cell.height * 3 / 4)
            }
        }
        .frame(width: cell.width, height: cell.height)
        .scaleEffect(render.settleScale)
        // Soft drop shadow so each cell reads as a separate raised tile.
        .shadow(color: .black.opacity(0.6), radius: 2, y: 1.5)
    }
}

// MARK: - Tick sound (synthesised, ambient, never interrupts the user's audio)

private final class FlapAudio {
    private let engine = AVAudioEngine()
    private var players: [AVAudioPlayerNode] = []
    private var buffer: AVAudioPCMBuffer?
    private var idx = 0
    private(set) var available = false

    init() {
        do {
            let session = AVAudioSession.sharedInstance()
            // Ambient + mixWithOthers: respects the silent switch and never stops
            // music/podcasts the user already has playing.
            try session.setCategory(.ambient, options: [.mixWithOthers])
            try session.setActive(true)

            let sampleRate = 44_100.0
            guard let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 1)
            else { return }

            let frames = AVAudioFrameCount(0.008 * sampleRate) // ~8ms mechanical tick
            guard let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
                  let data = buf.floatChannelData
            else { return }
            buf.frameLength = frames
            let n = Int(frames)
            for i in 0..<n {
                let t = Double(i) / sampleRate
                let env = exp(-t * 950)                       // fast decay
                let tone = sin(2 * Double.pi * 2600 * t)      // tick pitch
                let click = i < 3 ? 1.0 : 0.0                 // sharp attack
                data[0][i] = Float((0.6 * tone + 0.4 * click) * env * 0.5)
            }
            buffer = buf

            for _ in 0..<8 {                                   // pool for overlapping ticks
                let p = AVAudioPlayerNode()
                engine.attach(p)
                engine.connect(p, to: engine.mainMixerNode, format: format)
                players.append(p)
            }
            try engine.start()
            players.forEach { $0.play() }
            available = true
        } catch {
            available = false
        }
    }

    func tick(volume: Float) {
        guard available, let buffer, !players.isEmpty else { return }
        let p = players[idx]
        idx = (idx + 1) % players.count
        p.volume = volume
        p.scheduleBuffer(buffer, at: nil, options: .interrupts, completionHandler: nil)
    }

    func stop() {
        guard available else { return }
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
    }
}

// MARK: - Preview

#Preview {
    SplitFlapLoadingView { print("split-flap: onComplete") }
}
