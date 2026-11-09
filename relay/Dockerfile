FROM golang:1.27-alpine@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS build
RUN apk add --no-cache git
WORKDIR /src
RUN git init . && git remote add origin https://github.com/AmbitiousJun/go-emby2openlist.git \
    && git fetch --depth 1 origin 9b049b885388108c61f234244c1249f9a52a8dd6 \
    && git checkout --detach FETCH_HEAD
COPY strict-direct.patch /tmp/strict-direct.patch
RUN git apply --check /tmp/strict-direct.patch && git apply /tmp/strict-direct.patch
COPY managed-token.patch /tmp/managed-token.patch
RUN git apply --check /tmp/managed-token.patch && git apply /tmp/managed-token.patch
COPY playback.go playback_test.go ./internal/service/emby/
COPY playback.patch /tmp/playback.patch
RUN git apply --check /tmp/playback.patch && git apply /tmp/playback.patch
# ponytail: relay admin UI is disabled; restore upstream web build if enabling it.
# Emby's movie wall and the custom search entry do not use this optional UI.
COPY LICENSE.upstream web/dist/disabled.txt
RUN go test ./internal/web ./internal/service/emby ./internal/service/m3u8 ./internal/service/openlist -run 'TestStrict' \
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

