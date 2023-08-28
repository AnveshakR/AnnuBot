FROM python:3.10.11

WORKDIR /app

RUN apt-get update && apt-get install -y git build-essential lzma ffmpeg

RUN git clone https://github.com/AnveshakR/AnnuBot.git AnnuBot

WORKDIR /app/AnnuBot

RUN pip install -r /app/AnnuBot/requirements.txt

CMD [ "python", "annubot.py" ]
