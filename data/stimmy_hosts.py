"""Hosts for QP events (/stimmy and the passive QP-reward drop): wealth/generosity-themed
servants, each with a transparent portrait in assets/stimmy/, in-character "here's some QP"
lines, and a wealth tier `qp = (min, mode, max)`. The random QP-reward amount is a triangular
roll over that tier, so a rich host (Gilgamesh) pays far more than a poor one (Jinako); /stimmy
ignores the tier and just uses the host + a line. Rosters ported from the Bunyan bot's qp_reward
+ /beg events; the /beg lines are reworded here for a "found QP" context (not begging)."""

STIMMY_HOSTS = {
    "jinako": {
        "name": "Jinako Carigiri",
        "image": "jinako.png",
        "qp": (10, 30, 80),
        "lines": [
            "M-Master! I was organizing my room and found this! You can have it!",
            "Umm, Master? I don't need this... so... here?",
            "Eh?! This was just lying around! Better give it to you, Master!",
            "I-I'm not a NEET! I was productive and found this for you!",
            "Master! I leveled up in my game and found real QP too! Take it!",
            "Hehe~ Found this while cleaning! Well, you can have it, Master~",
        ],
    },
    "mash": {
        "name": "Mash Kyrielight",
        "image": "mash.png",
        "qp": (40, 100, 250),
        "lines": [
            "Senpai! I found some QP while tidying the supply room. Please, take it!",
            "Um, Senpai? I set a little QP aside for you. I hope it helps!",
            "I want to support you however I can, Senpai. Here, some QP!",
            "Please accept this, Senpai. I'll always look out for you!",
        ],
    },
    "cu": {
        "name": "Cu Chulainn",
        "image": "cu.png",
        "qp": (40, 120, 300),
        "lines": [
            "Hah! Came across some coin on a hunt. Take it, it's yours.",
            "Here, kid. Found this lying around. Put it to good use.",
            "No need to thank me. Just spare QP from a good day's work.",
            "Oi, catch. A little something to keep you going.",
        ],
    },
    "dantes": {
        "name": "Edmond Dantes",
        "image": "dantes.png",
        "qp": (60, 180, 450),
        "lines": [
            "Hahaha! Master, I found this while wandering in the darkness. Take it!",
            "Wait and Hope, Master! I discovered this during my patrol!",
            "The Count of Monte Cristo brings you treasure, Master!",
            "Consider this a gift from the cavern king, Master!",
            "Even in this place, fortune smiles upon us. Here, Master!",
            "Vengeance requires funding. I found this for you, Master!",
        ],
    },
    "sheba": {
        "name": "Queen of Sheba",
        "image": "sheba.png",
        "qp": (80, 200, 500),
        "lines": [
            "Ufufu~ Master, business was good today! Here's your share!",
            "A queen knows how to manage wealth! I made some profit for you, Master!",
            "Master! I found a wonderful deal! The profits are yours~",
            "Ehehe~ Your clever queen made some wise investments! Here!",
            "Master, Master! Look what I earned for us! Aren't I amazing?",
            "The Queen of Sheba's business acumen pays off again! Take this, Master!",
        ],
    },
    "bb": {
        "name": "BB",
        "image": "bb.png",
        "qp": (80, 220, 550),
        "lines": [
            "Ufufu~ BB-chan found some spare QP just for you, Senpai~",
            "Lucky you! I felt generous today, so here's a little gift~",
            "Don't say BB-chan never gives you anything, Senpai! Take it~",
            "Teehee~ A present from your favorite kouhai. Enjoy it~",
        ],
    },
    "koyanskaya": {
        "name": "Koyanskaya",
        "image": "koyanskaya.png",
        "qp": (120, 350, 750),
        "lines": [
            "Ara ara~ A little something from my latest venture. Consider it a gift~",
            "Business was profitable today. I'll share a portion with you~",
            "Fufu~ Don't get the wrong idea. This is simply an investment in you~",
            "My, my. Take this, and remember who was so generous~",
        ],
    },
    "gilgamesh": {
        "name": "Gilgamesh",
        "image": "gilgamesh.png",
        "qp": (250, 500, 1000),
        "lines": [
            "Mongrel! I had some spare change lying around. You may have it.",
            "The King of Heroes has no need for such pittance. Take it, mongrel.",
            "Don't get used to this, mongrel. I simply had too much in my vault.",
            "Consider yourself fortunate! The treasury of Babylon overflows!",
            "Hmph! I found this trinket. A king has no use for such trifles.",
            "Rejoice, mongrel! Your king bestows a gift upon you!",
        ],
    },
}
