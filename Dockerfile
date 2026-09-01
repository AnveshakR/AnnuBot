# 3.11: yt-dlp deprecates 3.10 (recurring "Support for Python version 3.10 has
# been deprecated" warning in the logs). davey (voice codec) ships cp311
# manylinux x86_64 wheels, so the voice stack is unaffected.
FROM python:3.11.9

WORKDIR /app

RUN apt-get update && apt-get install -y git build-essential lzma ffmpeg

# Pin the clone to an exact commit (build-arg) so each new commit invalidates
# this layer and the image actually rebuilds. A bare `git clone` is a cached
# layer — rebuilds silently reuse the stale checkout.
ARG COMMIT=HEAD
RUN git clone https://github.com/AnveshakR/AnnuBot.git AnnuBot \
    && git -C AnnuBot checkout "$COMMIT"

WORKDIR /app/AnnuBot

RUN pip install -r /app/AnnuBot/requirements.txt

CMD [ "python", "annubot.py" ]
