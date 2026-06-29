//
//  TimeOfDay.swift
//  Headlines
//
//  The single source of truth for the time-of-day period. Home reads it for the
//  greeting; when the briefing/generation pipeline lands, the edition label
//  (e.g. "MORNING BRIEFING") will read from the SAME enum via a future
//  `briefingEdition` property — so the greeting and the edition can never drift
//  out of sync.
//

import Foundation

enum TimeOfDay {
    case morning      // 05:00–11:59
    case afternoon    // 12:00–17:59
    case evening      // 18:00–21:59
    case night        // 22:00–04:59 (wraps past midnight)

    /// The current period, derived from the hour in local time.
    static func current(_ date: Date = Date(), calendar: Calendar = .current) -> TimeOfDay {
        let hour = calendar.component(.hour, from: date)
        switch hour {
        case 5..<12:  return .morning
        case 12..<18: return .afternoon
        case 18..<22: return .evening
        default:      return .night     // 22, 23, 0–4
        }
    }

    /// The board-caps greeting word for this period (no trailing comma — the
    /// caller owns layout/punctuation).
    var greeting: String {
        switch self {
        case .morning:   return "GOOD MORNING"
        case .afternoon: return "GOOD AFTERNOON"
        case .evening:   return "GOOD EVENING"
        case .night:     return "GOOD NIGHT"
        }
    }

    // Future: `var briefingEdition: String` (e.g. "MORNING BRIEFING" /
    // "EVENING BRIEFING") will live here too, so the bulletin edition label and
    // this greeting always share one source. Not built yet.
}
