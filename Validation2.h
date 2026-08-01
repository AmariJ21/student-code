#ifndef VALIDATION2_H
#define VALIDATION2_H

#include <string>

// Prompts until the user enters an integer within [min, max].
int getValidInt(const std::string& prompt, int min, int max);

// Prompts until the user enters a non-empty line of text.
std::string getValidString(const std::string& prompt);

#endif
