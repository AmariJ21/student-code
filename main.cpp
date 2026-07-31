/*
 * Name:
 * Date:
 * Description:
 *   TODO: Write a short description of what this program does.
 */

#include <iostream>
#include <vector>
#include <algorithm>
#include "Podcast.h"
#include "Validation.h"

using namespace std;

// Function prototypes - one function per menu option
void addPodcast(vector<Podcast>& list);
void viewPodcasts(const vector<Podcast>& list);
void searchPodcastByTitle(const vector<Podcast>& list);
void sortByDuration(vector<Podcast>& list);

int main() {
    vector<Podcast> podcasts;
    int choice;

    do {
        cout << "\n--- Podcast Manager Menu ---\n";
        cout << "1. Add Podcast\n";
        cout << "2. View Podcasts\n";
        cout << "3. Search by Title\n";
        cout << "4. Sort by Duration\n";
        cout << "5. Exit\n";

        // TODO: Replace the line below with a call to getValidInt()
        // from Validation.h so the menu choice is restricted to 1-5
        // and non-numeric input can't crash the program.
        cin >> choice;

        switch (choice) {
            case 1:
                addPodcast(podcasts);
                break;
            case 2:
                viewPodcasts(podcasts);
                break;
            case 3:
                searchPodcastByTitle(podcasts);
                break;
            case 4:
                sortByDuration(podcasts);
                break;
            case 5:
                cout << "Exiting...\n";
                break;
            default:
                cout << "Invalid choice.\n";
        }
    } while (choice != 5);

    return 0;
}

void addPodcast(vector<Podcast>& list) {
    // TODO:
    // 1. Ask the user for a title and host using getValidString().
    // 2. Ask the user for a duration (in minutes) using getValidInt().
    // 3. Create a new Podcast object with those three values.
    // 4. Add it to the vector with list.push_back(...).
    // 5. Print a confirmation message.
}

void viewPodcasts(const vector<Podcast>& list) {
    // TODO:
    // 1. If list.empty(), print a message and return.
    // 2. Otherwise, loop through the vector (use an index or range-based
    //    for loop) and print each podcast's title, host, and duration.
}

void searchPodcastByTitle(const vector<Podcast>& list) {
    // TODO:
    // 1. If list.empty(), print a message and return.
    // 2. Ask the user for a title to search for with getValidString().
    // 3. Loop through the vector looking for a Podcast whose getTitle()
    //    matches the search term.
    // 4. Print the podcast's details if found; otherwise print a
    //    "not found" message.
}

void sortByDuration(vector<Podcast>& list) {
    // TODO:
    // 1. If list.empty(), print a message and return.
    // 2. Call sort(list.begin(), list.end(), ...) from <algorithm>,
    //    passing a lambda that compares two Podcasts by getDuration()
    //    (ascending order). See the lab hint sheet for the exact pattern.
    // 3. Print a confirmation message.
}
