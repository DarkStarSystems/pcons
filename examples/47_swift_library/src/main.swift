import Geometry

let c = Circle(radius: 2.0)
print(describe(c))
if CommandLine.arguments.contains("--test") {
    // Trivial self-test used by the Test() target below.
    assert(Circle(radius: 1.0).area > 3.14)
    print("tests passed")
}
