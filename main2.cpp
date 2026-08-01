#include <iostream>
#include <vector>
#include <algorithm>
#include "Podcast.h"
#include "Validation.h"

using namespace std;

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

        choice = getValidInt("Enter choice: ", 1, 5);

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
        }
    } while (choice != 5);

    return 0;
}

void addPodcast(vector<Podcast>& list) {
    string title = getValidString("Enter podcast title: ");
    string host = getValidString("Enter host name: ");
    int duration = getValidInt("Enter duration (minutes): ", 1, 1000);

    list.push_back(Podcast(title, host, duration));
    cout << "Podcast added successfully.\n";
}

void viewPodcasts(const vector<Podcast>& list) {
    if (list.empty()) {
        cout << "No podcasts to display.\n";
        return;
    }

    cout << "\n--- Podcast List ---\n";
    for (size_t i = 0; i < list.size(); i++) {
        cout << i + 1 << ". " << list[i].getTitle()
             << " | Host: " << list[i].getHost()
             << " | Duration: " << list[i].getDuration() << " min\n";
    }
}

void searchPodcastByTitle(const vector<Podcast>& list) {
    if (list.empty()) {
        cout << "No podcasts to search.\n";
        return;
    }

    string searchTitle = getValidString("Enter title to search for: ");
    bool found = false;

    for (const Podcast& p : list) {
        if (p.getTitle() == searchTitle) {
            cout << "Found: " << p.getTitle()
                 << " | Host: " << p.getHost()
                 << " | Duration: " << p.getDuration() << " min\n";
            found = true;
            break;
        }
    }

    if (!found) {
        cout << "No podcast found with that title.\n";
    }
}

void sortByDuration(vector<Podcast>& list) {
    if (list.empty()) {
        cout << "No podcasts to sort.\n";
        return;
    }

    sort(list.begin(), list.end(), [](const Podcast& a, const Podcast& b) {
        return a.getDuration() < b.getDuration();
    });

    cout << "Podcasts sorted by duration.\n";
}
