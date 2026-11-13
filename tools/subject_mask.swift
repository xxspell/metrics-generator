// Subject matte via Apple Vision (macOS 14+): writes a greyscale PNG where
// white = foreground subject(s), black = background. Usage:
//   swift tools/subject_mask.swift <input image> <output mask.png>
import Foundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
guard args.count == 3 else { FileHandle.standardError.write("usage: subject_mask <in> <out.png>\n".data(using: .utf8)!); exit(2) }
let input = URL(fileURLWithPath: args[1]), output = URL(fileURLWithPath: args[2])
guard let src = CGImageSourceCreateWithURL(input as CFURL, nil),
      let cg = CGImageSourceCreateImageAtIndex(src, 0, [kCGImageSourceShouldAllowFloat: false] as CFDictionary) else {
    FileHandle.standardError.write("cannot read image\n".data(using: .utf8)!); exit(1)
}
let req = VNGenerateForegroundInstanceMaskRequest()
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([req])
    guard let obs = req.results?.first, !obs.allInstances.isEmpty else {
        FileHandle.standardError.write("no subject found\n".data(using: .utf8)!); exit(3)
    }
    let buf = try obs.generateScaledMaskForImage(forInstances: obs.allInstances, from: handler)
    let ci = CIImage(cvPixelBuffer: buf)
    let ctx = CIContext()
    try ctx.writePNGRepresentation(of: ci, to: output, format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
} catch {
    FileHandle.standardError.write("vision failed: \(error)\n".data(using: .utf8)!); exit(1)
}
