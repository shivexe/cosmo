// renders the Cosmo app icon: swift make_icon.swift <out.png>
import AppKit
import SwiftUI

struct IconView: View {
    let rim: [Color] = [0x7CC4FF, 0xB69CFF, 0xFF9ECF, 0xFFD08A, 0x7CF0D0, 0x7CC4FF].map {
        Color(.sRGB, red: Double(($0 >> 16) & 0xFF) / 255, green: Double(($0 >> 8) & 0xFF) / 255, blue: Double($0 & 0xFF) / 255)
    }
    var body: some View {
        ZStack {
            ZStack {
                LinearGradient(colors: [Color(white: 0.13), Color(white: 0.03)], startPoint: .top, endPoint: .bottom)
                Circle()
                    .fill(AngularGradient(colors: rim, center: .center, angle: .degrees(30)))
                    .frame(width: 560, height: 560)
                    .blur(radius: 70)
                    .opacity(0.55)
            }
            .frame(width: 824, height: 824)
            .clipShape(RoundedRectangle(cornerRadius: 185, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 185, style: .continuous).strokeBorder(Color.white.opacity(0.08), lineWidth: 3))
            Circle()
                .fill(RadialGradient(colors: [Color(white: 0.30), Color(white: 0.10), Color(white: 0.02)],
                                     center: UnitPoint(x: 0.34, y: 0.28), startRadius: 0, endRadius: 330))
                .frame(width: 460, height: 460)
            Circle()
                .strokeBorder(AngularGradient(colors: rim, center: .center, angle: .degrees(30)), lineWidth: 14)
                .frame(width: 460, height: 460)
            Ellipse()
                .fill(Color.white.opacity(0.10))
                .frame(width: 170, height: 90)
                .rotationEffect(.degrees(-28))
                .offset(x: -90, y: -120)
                .blur(radius: 6)
        }
        .frame(width: 1024, height: 1024)
    }
}

@MainActor
func render() {
    let renderer = ImageRenderer(content: IconView())
    renderer.scale = 1
    guard let image = renderer.nsImage, let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) else { fatalError("render failed") }
    try! png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
}

MainActor.assumeIsolated { render() }
