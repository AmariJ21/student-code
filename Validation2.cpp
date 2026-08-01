#include "Validation2.h"
#include <iostream>
#include <limits>

using namespace std;

int getValidInt(const string& prompt, int min, int max) {
    int value;

    while (true) {
        cout << prompt;
        cin >> value;

        if (cin.fail()) {
            cin.clear();
            cin.ignore(numeric_limits<streamsize>::max(), '\n');
            cout << "Invalid input. Please enter a whole number.\n";
            continue;
        }

        cin.ignore(numeric_limits<streamsize>::max(), '\n');

        if (value < min || value > max) {
            cout << "Please enter a number between " << min << " and " << max << ".\n";
            continue;
        }

        return value;
    }
}

string getValidString(const string& prompt) {
    string value;

    while (true) {
        cout << prompt;
        getline(cin, value);

        if (value.empty()) {
            cout << "Input cannot be empty. Please try again.\n";
            continue;
        }

        return value;
    }
}
