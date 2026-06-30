import SwiftUI

// Shared board typeface token.
//
// DejaVu Sans Bold (open-licensed), bundled via UIAppFonts in Info.plist.
// Use this on every board-style screen so the type stays consistent — caps,
// the split-flap loader, and any future board UI all route through `.board`.
extension Font {
    /// Board / flap text — DejaVu Sans Bold, CAPS. The split-flap world.
    static func board(_ size: CGFloat) -> Font {
        .custom("DejaVuSans-Bold", size: size)
    }

    /// Editorial reading serif — Newsreader (bundled), an FT-feel high-contrast
    /// serif. The one reading-text role on the light playback page (UP NEXT).
    /// Swappable for Tiempos later. Variable-font default instance is 16pt.
    static func editorial(_ size: CGFloat) -> Font {
        .custom("Newsreader16pt-Regular", size: size)
    }

    /// Metadata labels — a small letter-spaced grotesque (system sans), used
    /// uppercased with tracking ("NOW PLAYING", "SOURCES", "UP NEXT", …).
    static func label(_ size: CGFloat) -> Font {
        .system(size: size, weight: .semibold, design: .default)
    }
}
