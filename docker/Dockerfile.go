FROM dark-factory-base

ARG GO_VERSION
RUN apk add --no-cache go=${GO_VERSION}

ENV GOPATH=/home/agent/go
ENV PATH="$GOPATH/bin:$PATH"
