"""Grail-event hosts, ported from the Bunyan bot. Portraits are transparent PNGs in
assets/grail/. Lines take {user} = the claimer's display name."""
from __future__ import annotations

GRAIL_HOSTS: dict[str, dict] = {
    "irisviel": {
        "name": "Irisviel von Einzbern",
        "image": "irisviel.png",
        "single_appear": [
            "The vessel of the Grail... it has manifested once more.",
            "As the Holy Grail's vessel, I sense its presence nearby...",
            "The wish-granting chalice calls out to you, Master.",
            "A golden light emerges... The Grail seeks a worthy recipient.",
            "Master, the Holy Grail has appeared. Will you claim it?",
            "The miracle of the Grail... it awaits one who would reach for it.",
        ],
        "single_claim": [
            "{user}, the Grail has chosen you. Use its power wisely.",
            "The Holy Grail recognizes {user}'s resolve. Cherish this miracle.",
            "{user} has received the Grail's blessing. May your wish come true.",
            "As the Grail's vessel, I entrust this to you, {user}.",
            "{user}, you have claimed the Holy Grail. What servant shall transcend?",
            "The miracle passes to {user}. The Grail's power is now yours.",
        ],
        "box_appear": [
            "The Holy Grail's essence has taken a curious form... A present box filled with miracles.",
            "Master, I sense something extraordinary within this vessel. Multiple grails await...",
            "As the Grail's vessel, I have prepared something special. Each opening reveals a miracle.",
            "A treasure box blessed by the Grail itself... Claim what is rightfully yours, Master.",
            "The golden chalice has fragmented into this form. How many blessings will you receive?",
        ],
        "box_claim": [
            "{user}, the Grail has chosen you. Another miracle awaits.",
            "The Holy Grail recognizes {user}'s resolve. Cherish this blessing.",
            "{user} has received the Grail's gift. May your servant transcend.",
            "As the Grail's vessel, I am pleased to bestow this upon you, {user}.",
            "The miracle passes to {user}. Use its power wisely.",
        ],
    },
    "bb": {
        "name": "BB",
        "image": "bb.png",
        "single_appear": [
            "Ehe~ Senpai! BB found something shiny and golden~!",
            "Oh my, oh my! A Holy Grail materialized! How convenient~",
            "Senpai, Senpai! Look what your kouhai discovered!",
            "A wild Holy Grail appeared! ...Wait, wrong game. Anyway~!",
            "Ufufu~ The Grail came to BB! Must be fate, right Senpai?",
            "Breaking news! BB Channel presents: A Holy Grail giveaway!",
        ],
        "single_claim": [
            "{user} snatched it! Ehe~ BB knew you were quick, Senpai!",
            "Congratulations, {user}! Your kouhai is SO proud of you~!",
            "{user} claimed BB's special prize! Now go grail your waifu~",
            "Ooh, {user} got it! BB will remember this... positively, maybe!",
            "The Grail goes to {user}! Use /grail and thank your kouhai later~",
            "{user} wins! BB's generosity knows no bounds, doesn't it~?",
        ],
        "box_appear": [
            "Ehe~ Senpai! BB found a VERY special present box! It's full of grails!",
            "Oh my, oh my! A legendary treasure chest appeared! Multiple Holy Grails inside~!",
            "Senpai, Senpai! Your kouhai discovered the ultimate prize box!",
            "Breaking news on BB Channel! A rare Grail box has materialized! How many will you get?",
            "Ufufu~ This isn't just any present box, Senpai... It's a GRAIL present box!",
        ],
        "box_claim": [
            "{user} claimed a grail! Ehe~ BB is SO proud of you, Senpai!",
            "Another grail for {user}! Your kouhai's generosity knows no bounds~!",
            "{user} got a Holy Grail! Now go grail your favorite servant~",
            "Congratulations, {user}! That's another grail in the bag!",
            "Ooh, {user} is on a roll! BB approves of your grail hunting skills~!",
        ],
    },
    "gilgamesh": {
        "name": "Gilgamesh",
        "image": "gilgamesh.png",
        "single_appear": [
            "Hmph. Another Grail from my treasury has slipped out.",
            "Mongrels! One of my treasures has deigned to appear before you.",
            "The Holy Grail... Even this is but a trinket in the Gate of Babylon.",
            "Rejoice! The King of Heroes offers a Grail from his vault!",
            "This Grail seeks entertainment. Show me who among you is worthy.",
            "A golden chalice descends! Prove your worth, mongrels!",
        ],
        "single_claim": [
            "{user}! You dare claim a treasure of the king? ...Very well.",
            "Hmph, {user} was fastest. Perhaps you're not entirely worthless.",
            "The Grail goes to {user}. Do not disappoint me, mongrel.",
            "{user} claimed my treasure! I expect great things from this investment.",
            "So {user} took it. The King of Heroes acknowledges your boldness!",
            "{user}! You have earned this Grail. Now entertain me with its use!",
        ],
        "box_appear": [
            "Hmph! The Gate of Babylon has released a chest of Holy Grails. Consider yourselves fortunate!",
            "Mongrels! Multiple treasures from my vault have materialized. Claim them if you dare!",
            "The King of Heroes grows generous today. A box of grails awaits the worthy!",
            "Rejoice! I am distributing Holy Grails from my treasury. Don't disappoint me!",
            "These trinkets bore me. Take them, mongrels. Each box reveals another Grail!",
        ],
        "box_claim": [
            "{user}! You dare claim another treasure? ...Very well, take it.",
            "Hmph, {user} again. At least you're entertaining me with your greed!",
            "The Grail goes to {user}. The King acknowledges your boldness!",
            "{user} claimed my treasure! Don't make me regret this generosity.",
            "So {user} wants more. Fine! A king can afford to be magnanimous!",
        ],
    },
    "draco": {
        "name": "Draco",
        "image": "draco.png",
        "single_appear": [
            "Umu! The Beast has procured a Holy Grail for her Master!",
            "Behold! A Grail worthy of Rome... and of course, myself!",
            "Master! Your beloved Beast brings gifts of golden splendor!",
            "A Holy Grail appears! Truly, fortune favors those near Draco!",
            "Umu umu! This radiant chalice matches my own brilliance!",
            "The Mother of Harlots bestows a Grail upon this gathering!",
        ],
        "single_claim": [
            "Umu! {user} has claimed the Grail! A decision worthy of praise!",
            "{user} receives Draco's blessing! Go forth and grail gloriously!",
            "Splendid, {user}! The Beast approves of your swift action!",
            "{user} grasped the Grail! Umu, your taste is impeccable!",
            "The Grail belongs to {user}! May your servant shine like Rome!",
            "{user}! You have earned the favor of the Beast! Use it wisely, umu!",
        ],
        "box_appear": [
            "Umu! The Beast has prepared a magnificent treasure chest of Holy Grails!",
            "Behold! Multiple Grails worthy of Rome itself! Come, claim your prizes!",
            "Master! Your beloved Beast brings a chest overflowing with miracles!",
            "Umu umu! A rare treasure box of Holy Grails! How splendid!",
            "The Mother of Harlots bestows a bounty of Grails upon this gathering!",
        ],
        "box_claim": [
            "Umu! {user} claims another Grail! Your taste is impeccable!",
            "{user} receives Draco's blessing once more! Go forth and grail gloriously!",
            "Splendid, {user}! Another Grail for your collection!",
            "{user} grasped the Grail! The Beast approves wholeheartedly!",
            "The Grail belongs to {user}! May your servants shine like Rome, umu!",
        ],
    },
}


# Ember-of-Wisdom drop announcers -- Chaldea staff rather than the grail cast. Each uses its own
# Atlas face (looked up by servant_id at drop time), so no local art is needed. {user} = claimer.
EMBER_HOSTS: dict[str, dict] = {
    "mash": {
        "name": "Mash Kyrielight",
        "servant_id": 800100,
        "appear": [
            "Senpai, a cinder of pure knowledge just materialized! A Servant could grow from it.",
            "Analysis complete: crystallized wisdom. Perfect for training a Servant, Senpai!",
            "Something warm and glowing appeared... it feels like it wants to teach someone.",
        ],
        "claim": [
            "There you go, {user}! Put it toward a Servant's growth, okay?",
            "{user} secured it! I'm sure your Servant will grow stronger.",
            "Well done, {user}! Wisdom like this shouldn't go to waste.",
        ],
    },
    "davinci_caster": {
        "name": "Leonardo da Vinci",
        "servant_id": 500900,
        "appear": [
            "Ta-da! A little masterpiece of condensed experience, courtesy of yours truly.",
            "Behold, crystallized wisdom! Even a genius appreciates a good shortcut.",
            "I whipped this up between projects. Pure growth, no assembly required.",
        ],
        "claim": [
            "Nicely done, {user}! Feed it to a Servant and watch them flourish.",
            "{user} claims it! Efficiency -- I approve wholeheartedly.",
            "There you are, {user}. Wisdom is wasted sitting around, after all.",
        ],
    },
    "davinci_rider": {
        "name": "Leonardo da Vinci (Rider)",
        "servant_id": 403500,
        "appear": [
            "Step right up~! One glowing ember of wisdom, absolutely free!",
            "Limited-time offer: crystallized experience, going fast! Who wants it?",
            "Hehe, look what rolled off the workshop cart~ Grab it before it cools!",
        ],
        "claim": [
            "Sold~! Enjoy the gains, {user}!",
            "{user} snags the prize! Come again~",
            "Great pick, {user}! Your Servant's about to grow in style.",
        ],
    },
    "holmes": {
        "name": "Sherlock Holmes",
        "servant_id": 900500,
        "appear": [
            "Curious -- a nugget of crystallized insight. Its use is elementary enough.",
            "Observe: a lump of pure wisdom. Its purpose is deduced with ease.",
            "A rare specimen of condensed experience. Do try to make use of it.",
        ],
        "claim": [
            "Well reasoned, {user}. Apply it where it does the most good.",
            "{user} secures the evidence. A sound investment in growth.",
            "The case of the idle wisdom is closed -- {user} put it to work.",
        ],
    },
    "nemo": {
        "name": "Captain Nemo",
        "servant_id": 403700,
        "appear": [
            "...A cinder of wisdom surfaced. Salvage it.",
            "Found this in the depths. It will fuel a Servant's growth. Take it.",
            "Rare cargo -- crystallized experience. Don't let it sink.",
        ],
        "claim": [
            "Good. {user} has it. Put it to use.",
            "{user} claimed the haul. It won't go to waste on my watch.",
            "Secured by {user}. Growth is the only course worth charting.",
        ],
    },
    "asclepius": {
        "name": "Asclepius",
        "servant_id": 504300,
        "appear": [
            "A concentrate of vital wisdom -- excellent for a Servant's development.",
            "This ember carries the spark of growth. Administer it wisely.",
            "Consider it a prescription: pure experience, to strengthen the worthy.",
        ],
        "claim": [
            "Take it, {user}. A well-trained Servant is a healthy one.",
            "{user} receives the remedy. Growth is its own kind of healing.",
            "There, {user}. See that it nourishes a Servant properly.",
        ],
    },
}
