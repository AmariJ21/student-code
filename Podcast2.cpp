#include "Podcast2.h"
Podcast::Podcast() : title(""), host(""), duration(0) {}

Podcast::Podcast(std::string title, std::string host, int duration)
    : title(title), host(host), duration(duration) {}

std::string Podcast::getTitle() const {
    return title;
}

std::string Podcast::getHost() const {
    return host;
}

int Podcast::getDuration() const {
    return duration;
}

void Podcast::setTitle(const std::string& title) {
    this->title = title;
}

void Podcast::setHost(const std::string& host) {
    this->host = host;
}

void Podcast::setDuration(int duration) {
    this->duration = duration;
}
