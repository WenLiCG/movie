FROM golang:1.27.4-alpine@sha256:70bd7ddea8b6cbe5d6943cde279d6b67feb10cc924fb9100d1d8757c1575b280 AS build
RUN apk add --no-cache git
WORKDIR /src
RUN git init . && git remote add origin https://github.com/AmbitiousJun/go-emby2openlist.git \
    && git fetch --depth 1 origin 9b049b885388108c61f234244c1249f9a52a8dd6 \
    && git checkout --detach FETCH_HEAD
COPY dependencies.patch /tmp/dependencies.patch
RUN git apply /tmp/dependencies.patch
COPY strict-direct.patch /tmp/strict-direct.patch
RUN git apply --check /tmp/strict-direct.patch && git apply /tmp/strict-direct.patch
COPY managed-token.patch /tmp/managed-token.patch
RUN git apply --check /tmp/managed-token.patch && git apply /tmp/managed-token.patch
COPY cinema_mounts.go cinema_mounts_test.go ./internal/service/openlist/localtree/
COPY playback.go playback_test.go ./internal/service/emby/
COPY playback.patch /tmp/playback.patch
RUN git apply --check /tmp/playback.patch && git apply /tmp/playback.patch
# ponytail: relay admin UI is disabled; restore upstream web build if enabling it.
# Emby's movie wall and the custom search entry do not use this optional UI.
COPY LICENSE.upstream web/dist/disabled.txt
RUN go test ./internal/web ./internal/service/emby ./internal/service/m3u8 ./internal/service/openlist ./internal/service/openlist/localtree -run 'TestStrict' \
    && CGO_ENABLED=0 go build -tags=goexperiment.jsonv2 -trimpath \
       -ldflags='-X main.ginMode=release' -o /relay .

FROM alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f
RUN apk add --no-cache ca-certificates tzdata \
    && mkdir -p /app/lib /app/openlist-local-tree /app/custom-js /app/custom-css \
    && chown -R 1000:1000 /app
ENV TZ=Asia/Shanghai
WORKDIR /app
COPY --from=build /relay ./main
COPY --from=build /src/LICENSE ./LICENSE.upstream
USER 1000:1000
EXPOSE 8095
CMD ["./main"]

