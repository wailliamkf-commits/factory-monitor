import AppKit
import CoreMedia
import CoreVideo
import Foundation
import ScreenCaptureKit

private func writeStandardOutput(_ data: Data) {
    FileHandle.standardOutput.write(data)
}

private func appendBigEndian<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
    var encoded = value.bigEndian
    withUnsafeBytes(of: &encoded) { data.append(contentsOf: $0) }
}

final class FrameOutput: NSObject, SCStreamOutput, @unchecked Sendable {
    private let lock = NSLock()

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .screen,
              sampleBuffer.isValid,
              let pixelBuffer = sampleBuffer.imageBuffer else { return }
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let rowBytes = width * 4
        var pixels = Data(capacity: rowBytes * height)
        for row in 0..<height {
            pixels.append(base.advanced(by: row * bytesPerRow).assumingMemoryBound(to: UInt8.self), count: rowBytes)
        }
        var packet = Data(capacity: 20 + pixels.count)
        appendBigEndian(Date().timeIntervalSince1970.bitPattern, to: &packet)
        appendBigEndian(UInt32(width), to: &packet)
        appendBigEndian(UInt32(height), to: &packet)
        appendBigEndian(UInt32(pixels.count), to: &packet)
        packet.append(pixels)
        lock.lock()
        writeStandardOutput(packet)
        lock.unlock()
    }
}

@main
struct ScreenCaptureKitHelper {
    static func main() async {
        let arguments = CommandLine.arguments
        if arguments.contains("--help") {
            print("ScreenCaptureKit exact-window BGRA stream helper")
            return
        }
        guard let titleIndex = arguments.firstIndex(of: "--window-title"), titleIndex + 1 < arguments.count else {
            FileHandle.standardError.write(Data("--window-title is required\n".utf8))
            exit(2)
        }
        let title = arguments[titleIndex + 1]
        // ScreenCaptureKit's window path touches WindowServer APIs that require
        // CoreGraphics/AppKit initialization in a command-line executable.
        _ = NSApplication.shared
        let fps: Int32
        if let fpsIndex = arguments.firstIndex(of: "--fps"), fpsIndex + 1 < arguments.count {
            fps = Int32(arguments[fpsIndex + 1]) ?? 5
        } else {
            fps = 5
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let matches = content.windows.filter { $0.title == title }
            guard matches.count == 1, let window = matches.first else {
                throw NSError(
                    domain: "FactoryMonitorCapture",
                    code: 2,
                    userInfo: [NSLocalizedDescriptionKey: "expected exactly one on-screen window titled '\(title)', found \(matches.count)"]
                )
            }
            let configuration = SCStreamConfiguration()
            configuration.width = max(1, Int(window.frame.width))
            configuration.height = max(1, Int(window.frame.height))
            configuration.minimumFrameInterval = CMTime(value: 1, timescale: max(1, fps))
            configuration.queueDepth = 3
            configuration.pixelFormat = kCVPixelFormatType_32BGRA
            configuration.showsCursor = false
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
            let output = FrameOutput()
            try stream.addStreamOutput(output, type: .screen, sampleHandlerQueue: DispatchQueue(label: "factory-monitor.capture"))
            try await stream.startCapture()
            FileHandle.standardError.write(Data("READY window_id=\(window.windowID)\n".utf8))
            while true {
                try await Task.sleep(for: .seconds(3600))
            }
        } catch {
            FileHandle.standardError.write(Data("ScreenCaptureKit failure: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
}
