import SwiftUI

// Shared board typeface token.
//
// DejaVu Sans Bold (open-licensed), bundled via UIAppFonts in Info.plist.
// Use this on every board-style screen so the type stays consistent — caps,
// the split-flap loader, and any future board UI all route through `.board`.
extension Font {
    static func board(_ size: CGFloat) -> Font {
        .custom("DejaVuSans-Bold", size: size)
    }
}
