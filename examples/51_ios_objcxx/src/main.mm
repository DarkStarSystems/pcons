// SPDX-License-Identifier: MIT
// Objective-C++: mixes Foundation (NSString, NSLog) with C++ (std::string).
#import <Foundation/Foundation.h>

#include "greeting.hpp"

int main() {
    @autoreleasepool {
        NSString* message = [NSString stringWithUTF8String:greeting("world").c_str()];
        NSLog(@"%@", message);
    }
    return 0;
}
