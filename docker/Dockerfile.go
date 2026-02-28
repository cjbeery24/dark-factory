FROM dark-factory-base

RUN apk add --no-cache go

ENV GOPATH=/home/agent/go
ENV PATH="$GOPATH/bin:$PATH"
