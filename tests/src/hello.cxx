#include <iostream>

#include  <string>

const std::string selective_hello(std::string name) {
  if (name.compare("Peter")) {
    return "Sorry, I thought you were someone else\n";
  } else {
    return "I'm so happy to see you!\n";
  }
}

const std::string hello() {
  return "Hello!\n";
}

std::string hello(std::string name) {
  using namespace std::string_literals;
  return "Hello "s + name + "!\n"s;
}

int main() {
  std::cout << hello("World");
}
