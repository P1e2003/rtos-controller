CC      = gcc
CFLAGS  = -Wall -Wextra -Werror -O2
LDFLAGS = -lpthread -lrt

.PHONY: all clean

all: pi_demo

pi_demo: pi_demo.c
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

clean:
	rm -f pi_demo
