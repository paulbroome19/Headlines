//
//  FilterTreeView.swift
//  Headlines
//
//  The reusable filter tree: a scrollable, expandable list of TopicNodes with
//  a board-material tri-state toggle on every row. Onboarding's
//  BuildBriefingView and the in-app Filters screen both drop this in with a
//  FilterTreeModel — no per-call customisation.
//

import SwiftUI

// MARK: - Open chevron primitive ( "›" — two strokes, no base/fill )

/// A minimal open chevron used for the "enter" affordance and the row
/// expand/collapse control. Drawn as an un-closed path so it strokes as two
/// lines meeting at a point, never a filled triangle.
struct OpenChevron: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to:    CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        return p
    }
}

// MARK: - Board-material tri-state toggle

/// A select control rendered in the shared board material. `on` lights the
/// knob bone-white at the trailing edge, `off` dims it at the leading edge,
/// `partial` centres a dimmed knob with a lit centre dot.
struct BoardToggle: View {
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
        case .on:      return BoardColors.character.opacity(0.92)
        case .partial: return BoardColors.character.opacity(0.55)
        case .off:     return BoardColors.character.opacity(0.22)
        }
    }

    var body: some View {
        ZStack {
            // Track — board material, hinge-dark rim.
            Capsule()
                .fill(BoardColors.panelTopGradient)
                .overlay(
                    Capsule().strokeBorder(BoardColors.seam, lineWidth: 1)
                )
                .overlay(
                    Image(uiImage: BoardGrain.image)
                        .resizable(resizingMode: .tile)
                        .opacity(0.02)
                        .clipShape(Capsule())
                )
                .frame(width: trackW, height: trackH)

            // Knob.
            ZStack {
                Circle().fill(knobColor)
                if state == .partial {
                    // Lit centre dot reads "some, not all".
                    Circle()
                        .fill(BoardColors.character.opacity(0.95))
                        .frame(width: knobD * 0.34, height: knobD * 0.34)
                }
            }
            .frame(width: knobD, height: knobD)
            .shadow(color: .black.opacity(0.5), radius: 1.5, y: 1)
            .offset(x: knobOffset)
        }
        .frame(width: trackW, height: trackH)
        .animation(.easeInOut(duration: 0.16), value: state)
    }
}

// MARK: - One row

private struct FilterRowView: View {
    let row: FilterTreeModel.Row
    let onToggleExpand: () -> Void
    let onToggleSelect: () -> Void

    private var fontSize: CGFloat {
        // Parents bolder/larger; deeper rows smaller.
        switch row.depth {
        case 0:  return 17
        case 1:  return 14
        default: return 12
        }
    }

    private var labelOpacity: Double {
        max(0.45, 0.95 - Double(row.depth) * 0.16)
    }

    var body: some View {
        HStack(spacing: 12) {
            // Left expand/collapse chevron — only on nodes with children.
            Group {
                if row.hasChildren {
                    Button(action: onToggleExpand) {
                        OpenChevron()
                            .stroke(BoardColors.character.opacity(0.6),
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

            Text(row.label)
                .font(.board(fontSize))
                .foregroundColor(BoardColors.character.opacity(labelOpacity))
                .tracking(1.5)

            Spacer(minLength: 8)

            // Right select toggle — on every row.
            Button(action: onToggleSelect) {
                BoardToggle(state: row.selection)
            }
            .buttonStyle(.plain)
        }
        .padding(.vertical, row.depth == 0 ? 16 : 12)
        .padding(.leading, 20 + CGFloat(row.depth) * 24)   // staggered indent
        .padding(.trailing, 22)
        // Subtle deepening tint per depth so nesting reads at a glance.
        .background(Color.white.opacity(Double(row.depth) * 0.022))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(BoardColors.seam.opacity(0.5))
                .frame(height: 0.75)
        }
        .contentShape(Rectangle())
        .animation(.easeInOut(duration: 0.2), value: row.isExpanded)
    }
}

// MARK: - The tree

struct FilterTreeView: View {
    @ObservedObject var model: FilterTreeModel

    var body: some View {
        LazyVStack(spacing: 0) {
            ForEach(model.visibleRows) { row in
                FilterRowView(
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
        BoardColors.background.ignoresSafeArea()
        ScrollView {
            FilterTreeView(model: FilterTreeModel(roots: TopicSeed.defaultTree()))
        }
    }
}
