// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "Cosmo",
    platforms: [.macOS("26.0")],
    targets: [
        .executableTarget(
            name: "Cosmo",
            path: "Sources/Cosmo",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
