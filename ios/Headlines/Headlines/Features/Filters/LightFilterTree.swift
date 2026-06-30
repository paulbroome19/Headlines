//
//  LightFilterTree.swift
//  Headlines
//
//  The LIGHT-register skin of the tri-state filter tree. It reuses the existing
//  `FilterTreeModel` logic VERBATIM (cascade, partial derivation, expand/collapse,
//  visibleRows, selectedLeafIDs) — only the presentation changes: ink type on the
//  near-white page, hairline dividers, and a monochrome toggle (filled dark = on,
//  outline grey = off, centred = partial). The dark `FilterTreeView` / `BoardToggle`
//  stay as-is for the board world; this is the parallel light presentation.
//

import SwiftUI

// MARK: - Light tri-state toggle

/// Monochrome select control for the light page:
/// • on      — filled dark (ink) track, bone knob at the trailing edge
/// • off      — outline-grey track, grey knob at the leading edge
/// • partial  — outline track, centred dark knob with a light centre dot
struct LightToggle: View {
    let state: NodeSelection

    private let trackW: CGFloat = 46
    private let trackH: CGFloat = 26
    private var knobD: CGFloat { trackH - 7 }

    private var knobOffset: CGFloat {
        let travel = (trackW - trackH) / 2
        switch state {
        case .off:     return -travel
        case .on:      return  travel
        case .partial: return  0
        }
    }

    private var knobColor: Color {
        switch state {
        case .on:      return LightColors.page              // bone knob on the dark track
        case .partial: return LightColors.ink               // dark centred knob (+ light dot)
        case .off:     return LightColors.ink.opacity(0.35) // grey knob
        }
    }

    var body: some View {
        ZStack {
            Capsule()
                .fill(state == .on ? LightColors.ink : Color.clear)
                .overlay(
                    Capsule().strokeBorder(
                        LightColors.ink.opacity(state == .on ? 0 : 0.3), lineWidth: 1.5)
                )
                .frame(width: trackW, height: trackH)

            ZStack {
                Circle().fill(knobColor)
                if state == .partial {
                    Circle()
                        .fill(LightColors.page)
                        .frame(width: knobD * 0.34, height: knobD * 0.34)
                }
            }
            .frame(width: knobD, height: knobD)
            .shadow(color: .black.opacity(state == .on ? 0.22 : 0.10), radius: 1.5, y: 1)
            .offset(x: knobOffset)
        }
        .frame(width: trackW, height: trackH)
        .animation(.easeInOut(duration: 0.16), value: state)
    }
}

// MARK: - One light row

private struct LightFilterRowView: View {
    let row: FilterTreeModel.Row
    let onToggleExpand: () -> Void
    let onToggleSelect: () -> Void

    private var fontSize: CGFloat {
        switch row.depth {
        case 0:  return 16
        case 1:  return 13
        default: return 12
        }
    }

    private var labelOpacity: Double {
        max(0.5, 0.92 - Double(row.depth) * 0.18)
    }

    var body: some View {
        HStack(spacing: 12) {
            // Left expand/collapse chevron — parent rows only.
            Group {
                if row.hasChildren {
                    Button(action: onToggleExpand) {
                        OpenChevron()
                            .stroke(LightColors.ink.opacity(0.5),
                                    style: StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round))
                            .frame(width: 7, height: 12)
                            .rotationEffect(.degrees(row.isExpanded ? 90 : 0))
                            .frame(width: 22, height: 22)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                } else {
                    Color.clear.frame(width: 22, height: 22)
                }
            }

            Text(row.label.uppercased())
                .font(.label(fontSize))
                .tracking(row.depth == 0 ? 1.6 : 1.2)
                .foregroundColor(LightColors.ink.opacity(labelOpacity))

            Spacer(minLength: 8)

            // Right select toggle — on every row.
            Button(action: onToggleSelect) {
                LightToggle(state: row.selection)
            }
            .buttonStyle(.plain)
        }
        .padding(.vertical, row.depth == 0 ? 15 : 11)
        .padding(.leading, BottomActionBar.pageMargin + CGFloat(row.depth) * 22)   // staggered indent
        .padding(.trailing, BottomActionBar.pageMargin)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(LightColors.ink.opacity(0.08))
                .frame(height: 1)
        }
        .contentShape(Rectangle())
        .animation(.easeInOut(duration: 0.2), value: row.isExpanded)
    }
}

// MARK: - The light tree

/// Drop-in light tree — same `FilterTreeModel`, same mechanics, light skin.
struct LightFilterTreeView: View {
    @ObservedObject var model: FilterTreeModel

    var body: some View {
        LazyVStack(spacing: 0) {
            ForEach(model.visibleRows) { row in
                LightFilterRowView(
                    row: row,
                    onToggleExpand: { model.toggleExpand(row.id) },
                    onToggleSelect: { model.toggleSelect(row.id) }
                )
            }
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        LightColors.page.ignoresSafeArea()
        ScrollView {
            LightFilterTreeView(model: FilterTreeModel(roots: TopicSeed.defaultTree()))
        }
    }
}
