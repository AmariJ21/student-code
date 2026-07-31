#include "Validation.h"
#include <iostream>
#include <limits>

using namespace std;

int getValidInt(const string& prompt, int min, int max) {
    // TODO:
    // 1. Print the prompt and read an int into a local variable with cin.
    // 2. If cin.fail() is true, the user typed something non-numeric:
    //      - call cin.clear() to reset the error flags
    //      - call cin.ignore(numeric_limits<streamsize>::max(), '\n')
    //        to discard the bad input still sitting in the buffer
    //      - print an error message and loop back to step 1
    // 3. After a successful read, still call cin.ignore(...) once to
    //    clear any leftover newline character from the buffer.
    // 4. If the value is less than min or greater than max, print an
    //    error message and loop back to step 1.
    // 5. Otherwise, return the value.
    //
    // Hint: wrap all of this in a `while (true) { ... }` loop and
    // `return` only when the value is valid.

    return 0; // placeholder so the project compiles until you implement this
}

string getValidString(const string& prompt) {
    // TODO:
    // 1. Print the prompt and read a full line into a local variable
    //    using getline(cin, variable).
    // 2. If the line is empty, print an error message and loop back.
    // 3. Otherwise, return the value.

    return ""; // placeholder so the project compiles until you implement this
}
