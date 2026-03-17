.PHONY: build install clean

build:
	go build -o kedge ./cmd/kedge

install:
	go install ./cmd/kedge

clean:
	rm -f kedge
