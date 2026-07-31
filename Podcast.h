#ifndef PODCAST_H
#define PODCAST_H

#include <string>

// Represents a single podcast entry with a title, host, and duration.
class Podcast {
private:
    std::string title;
    std::string host;
    int duration; // duration in minutes

public:
    Podcast();
    Podcast(std::string title, std::string host, int duration);

    std::string getTitle() const;
    std::string getHost() const;
    int getDuration() const;

    void setTitle(const std::string& title);
    void setHost(const std::string& host);
    void setDuration(int duration);
};

#endif
