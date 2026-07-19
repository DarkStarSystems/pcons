// Same module as geometry.swift: no import needed to see Circle.
public func describe(_ c: Circle) -> String {
    "Circle r=\(c.radius) area=\(String(format: "%.2f", c.area))"
}

import Foundation  // for String(format:)
