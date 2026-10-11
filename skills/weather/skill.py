import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        import requests

        resp = requests.get(
            f"https://wttr.in/{quote(ns.location)}?format=j1",
            headers={"User-Agent": "curl/8.0"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        cur = data["current_condition"][0]
        forecast = [
            {
                "date": d["date"],
                "max_c": d["maxtempC"],
                "min_c": d["mintempC"],
                "desc": d["hourly"][4]["weatherDesc"][0]["value"],
            }
            for d in data.get("weather", [])
        ]
        emit(True, {
            "location": ns.location,
            "current": {
                "temp_c": cur["temp_C"],
                "feels_like_c": cur["FeelsLikeC"],
                "desc": cur["weatherDesc"][0]["value"],
                "humidity": cur["humidity"],
                "wind_kmph": cur["windspeedKmph"],
            },
            "forecast": forecast,
        })
    except Exception as e:
        emit(False, error=f"weather lookup failed: {e}")


if __name__ == "__main__":
    main()