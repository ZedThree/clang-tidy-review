#include <iostream>

#include  <string>

std::string hello(std::string name) {
  using namespace std::string_literals;
  return "Hello "s + name + "!\n"s;
}

int main() {
  std::cout << hello("World");
}
