public struct Circle {
    public let radius: Double
    public init(radius: Double) { self.radius = radius }
    public var area: Double { Double.pi * radius * radius }
}
