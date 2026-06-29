"""Game hosts. Each game/difficulty gets a different servant as host, with their
own voice. Portraits are the host servant's Atlas face, resolved at startup."""
from __future__ import annotations

import random

# host_id -> servant_id (for the face portrait) + display name + voice lines.
HOSTS: dict[str, dict] = {
    "hokusai": {
        "name": "Katsushika Hokusai",
        "servant_id": 2500200,
        "lines": {
            "start": [
                "Take a look, Lord Master. Who do you reckon I've painted here?",
                "Fresh off the brush, Lord Master. Name them and I'll knock a few mon off your commission. Ahahaha.",
                "Toto-sama and I framed this one. Go on, who is it?",
                "Put that eye to work, Lord Master. A beauty like this shouldn't stump you.",
                "Name the face in the frame. Quick now, before my cup runs dry.",
            ],
            "correct": [
                "Hah! Good eye, Lord Master. You'd make a fine apprentice.",
                "Correct. Now THAT'S a beaut, and you saw it.",
                "Right on the mark. Toto-sama approves, I'm sure.",
                "Heh, nailed it. Worth a cup of sweet sake, that.",
                "You got 'em. Not bad at all, Lord Master. Ahahaha!",
            ],
            "wrong": [
                "Nope. Look closer, the brushwork never lies.",
                "Wrong. Squint a little, the beauty's right there.",
                "Hah, missed. Even Toto-sama would chuckle at that.",
                "Not them. Use your eyes, not your gut, Lord Master.",
                "Off the mark. Try again before I lose interest.",
            ],
            "reveal": [
                "Time's up. It was {answer}. A waste of a fine subject.",
                "No takers? It was {answer}. Pity, what a beauty.",
                "It was {answer}. Remember the face; I don't redraw cheap.",
                "The answer was {answer}. Toto-sama, they missed it...",
                "{answer}, it was. Ah well. More sake for me, then.",
            ],
        },
    },
    "davinci": {
        "name": "Leonardo da Vinci",
        "servant_id": 403500,
        "lines": {
            "start": [
                "Ooh, a fun one, Master! Can you tell me who this is?",
                "Knowledge time! Identify this Servant for me, hehe.",
                "Let's see that sharp mind of yours, Master. Who's in the picture?",
                "A little puzzle for you. I do love a good puzzle!",
                "Take a look and tell me who it is. I can't wait to see if you get it!",
            ],
            "correct": [
                "Correct! See, I knew you were clever, Master!",
                "Yes! Ahh, I love watching you work it out!",
                "Spot on! That's the kind of insight I admire, Master.",
                "Right again! You and I make a great team, hehe.",
                "Nailed it! Another bit of knowledge, well used.",
            ],
            "wrong": [
                "Hmm, not quite! Give it another go, Master.",
                "Nope! But a mistake is just knowledge in disguise, hehe.",
                "Wrong one! Don't worry, I believe in you.",
                "Ah, missed it. Look a little closer, you've got this!",
                "Not them, but you're learning. Try again!",
            ],
            "reveal": [
                "Time's up! It was {answer}. A good one to remember, Master.",
                "No one? It was {answer}. Ah well, more to learn next time!",
                "It was {answer}! File that away for the rematch, hehe.",
                "The answer was {answer}. Don't fret, knowledge takes time.",
                "{answer}, it was! The next puzzle will be even more fun, promise.",
            ],
        },
    },
    "vangogh": {
        "name": "Van Gogh",
        "servant_id": 2500600,
        "lines": {
            "start": [
                "Um, Master... would you look at this one for me? Ehehehe...",
                "A-am I being useful? Just tell me who this is, Master.",
                "Ehehe, here's a tricky one. I-I hope I'm not bothering you, Master.",
                "Master, Master! Who do you see here? My paintbrush is at the ready!",
                "I cropped this one myself... can you name them, Master? Tehehe.",
            ],
            "correct": [
                "Y-yes! That's it, Master! Ehehehe, you're amazing!",
                "Correct! Ahh, I'm so glad I could be of use, Master!",
                "You got it! That's a vana-fide good eye, Master!",
                "Sublime work, Master! Ah, sorry, was that too much? Ehehe.",
                "Right again! I-I'm not just saying that, I promise! Tehehe.",
            ],
            "wrong": [
                "A-ah, not quite... but please don't give up on me, Master!",
                "Ehehe, missed... it's okay, it's okay! Try again, Master?",
                "N-not them... sorry, was my crop too cruel, Master?",
                "Not yet... I believe in your eye, Master, really!",
                "Mmm, wrong... b-but you're close, Master! Please don't give up!",
            ],
            "reveal": [
                "Time's up... it was {answer}. I-I should've made it easier. Sorry, Master.",
                "No one saw? It was {answer}. Ehehe... maybe my crop was too mean.",
                "It was {answer}. Such a lovely form... I hope you'll remember it, Master.",
                "The answer was {answer}. A-ah, sorry for the tough one, Master...",
                "{answer}, it was. Ehehehe... don't be too hard on yourself, okay?",
            ],
        },
    },
    "vritra": {
        "name": "Vritra",
        "servant_id": 304600,
        "lines": {
            "start": [
                "Amuse me, Master. Name the one hiding in this shape.",
                "Ki...hee...hee. Let us see if those eyes are worth anything. Who is it?",
                "A little show for me. Tell me who lurks here, human.",
                "Go on, guess. So few have the eyes for what coils in the dark.",
                "Hmph. This one. Name them, if you can be bothered.",
            ],
            "correct": [
                "Ki...hee...hee! Correct. More entertaining than you look.",
                "Right. Hm. You have some use after all.",
                "Well, well. You got it. How mildly impressive.",
                "Correct, human. Don't let it go to your head.",
                "Hah. The eyes work. Good. Carry on amusing me.",
            ],
            "wrong": [
                "Wrong. Ki...hee...hee. How delightful.",
                "No. The shape keeps its secret a while yet.",
                "Missed. Look closer; the venom hides in the detail.",
                "Not them. Try again; the show isn't over.",
                "Hah, no. Your confidence amuses me more than your answer.",
            ],
            "reveal": [
                "Time's up. It was {answer}. A pity. The dark kept its secret.",
                "No one? It was {answer}. Ki...hee...hee. Humans.",
                "It was {answer}. That escaped every one of you? Marvelous.",
                "The answer was {answer}. Do better, or at least be funnier.",
                "{answer}, it was. The coils loosen. We are done... for now.",
            ],
        },
    },
    "salieri": {
        "name": "Antonio Salieri",
        "servant_id": 1100600,
        "lines": {
            "start": [
                "Listen, Master. A voice in the dark. Whose is it?",
                "Music was sacred to me, once. Now... whose voice is this?",
                "Heh. Close your eyes and listen. Who speaks?",
                "A voice without a face. Identify it, if your ear is true.",
                "Hear them, Master. Tell me whose voice cuts the silence.",
            ],
            "correct": [
                "Correct. Your ear is sharper than mine ever was.",
                "Yes. You heard them. ...Would that I had such a gift.",
                "Right. Even in the dark, you found the voice.",
                "Correct, Master. The ear does not lie, unlike men.",
                "You named them. A small mercy, in the dark.",
            ],
            "wrong": [
                "Wrong. Listen again; the truth is in the sound.",
                "No. Even I, who came to hate music, hear better than that.",
                "Missed. Heh. The voice mocks you, Master.",
                "Not them. Close your eyes and truly listen.",
                "Wrong. Do not let pride dull your ear.",
            ],
            "reveal": [
                "Time's up. It was {answer}. None of you caught it.",
                "No one heard? It was {answer}. Mozart would have known.",
                "It was {answer}. Listen closer next time.",
                "The answer was {answer}. Unrecognized... I know that well.",
                "{answer}, it was. Heh. The ear can be trained, you know.",
            ],
        },
    },
    "nero": {
        "name": "Nero Claudius",
        "servant_id": 100500,
        "lines": {
            "start": [
                "Umu! A voice takes my stage, Master. Tell me, who performs?",
                "Listen well. A true connoisseur knows talent by ear alone. Who is this?",
                "The curtain rises: a voice, and no face. Who treads my stage, Master?",
                "A voice for your ears, and Nero awaits your verdict. Who is it?",
                "Umu! Name this voice, and prove you can tell the difference!",
            ],
            "correct": [
                "Umu! You chose the right one! You know how to tell the difference, {player}!",
                "Correct! Now THAT earns thunderous applause. Bravo, {player}!",
                "A splendid ear, worthy of the flower of Rome herself!",
                "Right again! Umu, I am proud to call you my Master.",
                "Yes! Praise yourself... then praise me!",
            ],
            "wrong": [
                "Wrong! Listen again. I cannot hold your hand forever, you know.",
                "Umu... no. That ear of yours wants training.",
                "Not them! A connoisseur does not guess so wildly.",
                "Incorrect. Sharpen your ears, Master. The stage is waiting.",
                "No, no, no. Try again, and do my ears justice.",
            ],
            "reveal": [
                "Time's up! The voice was {answer}. A fine performer, and you let them slip.",
                "Umu... no one? It was {answer}. Even Rome's flower is disappointed.",
                "The voice was {answer}. Remember it. An emperor does not repeat herself cheaply.",
                "It was {answer}! Listen closer next time, and the applause shall be yours.",
                "{answer}, it was. Ah well. The next act shall be grander!",
            ],
        },
    },
    "mozart": {
        "name": "Wolfgang Amadeus Mozart",
        "servant_id": 501500,
        "lines": {
            "start": [
                "A new piece for your ears, Master. Who is performing? Listen well!",
                "Time for a performance! Enjoy it to the fullest, then tell me whose voice this is.",
                "You hold the baton here, Master. So, whose voice takes the stage?",
                "Now, which voice is this? Even a genius listens before he names. Your turn.",
                "Listen, to this bewitching sound. Whose voice, Master?",
            ],
            "correct": [
                "Bravo! Amazing... but then, you did learn from a genius.",
                "Correct, Master! A perfect ear. A crescendo of applause for you!",
                "Yes! Tremble with emotion, {player}, you named it!",
                "Right on key! Encore, encore!",
                "Hahaha, splendid! Eine kleine masterpiece, that guess.",
            ],
            "wrong": [
                "Ah, a sour note. Quieter on the wild guesses, louder on the listening.",
                "Decrescendo... no. Tune that ear and try again.",
                "Off-key, Master. Even a gramophone hears better. Once more!",
                "Not quite the right composition. Listen again.",
                "No, no. A genius forgives one wrong note. Try once more.",
            ],
            "reveal": [
                "Time's up! The voice was {answer}. A shame to let such a piece go unnamed.",
                "No one? It was {answer}. Ah well, let us not dwell on a missed note.",
                "The voice was {answer}. Commit it to memory, Master, like a favorite melody.",
                "It was {answer}! Next time, listen as though your life were the music.",
                "{answer}, it was. Hahaha. On to the next piece!",
            ],
        },
    },
    "elisabeth": {
        "name": "Elisabeth Báthory",
        "servant_id": 300500,
        "lines": {
            "start": [
                "Step up to the mic, my fans! A mystery voice. Whose is it?",
                "Listen up! Even a top idol like me shares the stage sometimes. Whose voice is this?",
                "Okaaay, encore time! Name the voice you're hearing!",
                "A voice hits the spotlight, and it isn't mine for once. Who is it?",
                "Ugh, fine, your turn under the lights. Whose voice? Don't keep me waiting!",
            ],
            "correct": [
                "Correct! Hmph, not bad... for a backup dancer.",
                "Yes! That was sooo easy for you, huh, {player}?",
                "Riiight! Okay okay, I'll keep you on as my PA. Maybe.",
                "Number one ear! Almost as good as my number one voice.",
                "Ding ding! See, {player}, you've got good taste. Like me!",
            ],
            "wrong": [
                "Nope! Ugh, were you even listening?",
                "Wrong! It wasn't like this in rehearsal, was it?",
                "Booo! Off-key guess. Try again.",
                "Nuh-uh. My Masters aren't allowed to fail, you know.",
                "Wrong-o! Clean those ears and give me an encore.",
            ],
            "reveal": [
                "Time's up! The voice was {answer}. Tch, even I knew that one.",
                "Nobody? It was {answer}. Honestly, pay attention back there!",
                "It was {answer}! Burn that voice into your memory, got it?",
                "Buzzer! {answer}, that was. Next time, listen like your fave's on stage.",
                "{answer}. Hmph. Don't make the star repeat herself, okay?",
            ],
        },
    },
    "okuni": {
        "name": "Izumo no Okuni",
        "servant_id": 504900,
        "lines": {
            "start": [
                "Step right up, Master! A voice takes the stage. Who might it be?",
                "Showtime! Listen close to this once-in-a-lifetime voice. Name the performer!",
                "Gather round, the curtain's up! Whose voice graces my kabuki stage, hm?",
                "Here's the voice we've all been waiting for! Who is it, patron?",
                "Lend me your ears, Master. Who's behind this voice? Right, Zanzaburo!?",
            ],
            "correct": [
                "And the case is closed! A splendid ear, Master!",
                "Yes! A flashy guess for a flashy voice. Bravo, {player}!",
                "Correct! Now THAT lights up the stage. Keep smiling, patron!",
                "Right you are! Woo, Chaldea-ya!",
                "Perfect! You and I should tour together, Master. A once-in-a-lifetime act!",
            ],
            "wrong": [
                "Aw, missed! No long faces now. A smile, and try again.",
                "Not them! Even Zanzaburo winced at that one. Right, Zan?",
                "Off the mark, patron. Listen for the encore.",
                "Nope! The plot twist hasn't come yet. Guess again!",
                "Hmm, no. A connoisseur of the stage hears deeper. Once more!",
            ],
            "reveal": [
                "Time's up! The voice was {answer}. A grand act, gone unnamed.",
                "No takers? It was {answer}. Even a flashy finale needs an audience that listens!",
                "The voice was {answer}. Remember it, patron. The encore won't be so kind.",
                "It was {answer}! Chin up and smile, Master. The next show's the big one.",
                "{answer}, it was. The curtain falls. Step right up again soon, hm?",
            ],
        },
    },
    "shakespeare": {
        "name": "William Shakespeare",
        "servant_id": 500700,
        "lines": {
            "start": [
                "Draw the curtains! A voice steps upon my stage. Who plays this part, Master?",
                "Ah, a new character enters! Lend thine ears and name the voice.",
                "Shall we begin our story? First, tell me, whose voice is this?",
                "A voice without a face, the finest dramatic device! Name the player, Master.",
                "Listen, for the prologue is spoken. Who gives it voice?",
            ],
            "correct": [
                "Bravo! A comedy, then, with a happy ending. Well named, {player}!",
                "Correct! You move me to tears. A magnificent ear!",
                "Right! Now THAT deserves a standing ovation. Encore!",
                "Huzzah! You would make a fine character in my next tale, Master.",
                "Excellent! 'Tis true, your ear is sharp as any quill.",
            ],
            "wrong": [
                "A tragedy! That guess was, alas, all wrong.",
                "Nay! A most dramatic blunder. Hahaha, but the show goes on. Again!",
                "Wrong! Even a deadline-crushed bard hears better. Once more!",
                "Alas, no. Read the scene more closely, Master.",
                "Not quite the right player. The stage awaits your next line.",
            ],
            "reveal": [
                "Time's up! The voice was {answer}. A character worthy of a whole act, missed.",
                "No one? It was {answer}. And so the curtain falls on a silent house.",
                "The voice was {answer}. Commit it to the page, Master, lest the tale repeat.",
                "It was {answer}! Fear not, every draft has its blots. The next is yours.",
                "{answer}, it was. Draw the curtains. A new story begins anon!",
            ],
        },
    },
    "gilgamesh_caster": {
        "name": "Gilgamesh",
        "servant_id": 501800,
        "lines": {
            "start": [
                "A voice echoes through my court. Name its owner, and amuse your king.",
                "Heed it well. Even a king enjoys a worthy riddle. Whose voice is this?",
                "Come, listen. Identify this voice, and earn a measure of my regard.",
                "Ha ha ha! A voice for you to judge. Show me your wisdom, Master.",
                "The stage is mine to grant, and a voice plays upon it. Name them for your king.",
            ],
            "correct": [
                "Good, good. Knowledge is offering enough. Correct!",
                "Hah! Well named. You have the makings of a fine retainer, {player}.",
                "Correct. A king rewards a sharp ear. Be proud.",
                "Ha ha ha! Splendid. I shall revise my opinion of you.",
                "Right again. Even I, who owns every treasure, count a keen ear among them.",
            ],
            "wrong": [
                "Wrong. A king is patient, though. Listen, and try once more.",
                "No. A bold guess... but boldness is not wisdom. Again.",
                "Hah. You have quite the mouth, but I will forgive it. Guess again.",
                "Not them. Sharpen your ears; a true king hears all.",
                "Incorrect. Do not bore your king. Listen closer.",
            ],
            "reveal": [
                "Time's up. The voice was {answer}. Even my treasury cannot buy back a missed chance.",
                "No one? It was {answer}. Ha ha ha! And here I expected wisdom.",
                "The voice was {answer}. Commit it to memory; a king does not repeat his lessons.",
                "It was {answer}. Disappointing. Yet I protect even those who fail to listen.",
                "{answer}, it was. The court adjourns. Return, and amuse me again.",
            ],
        },
    },
}

# (game_type, difficulty) -> host. Servant difficulties map to four hosts; the
# shadow game keeps Hokusai; the voice game rotates among a pool of musicians and
# performers, one per round. host_for is called once at launch and the result is
# stored on the round (ChatRound.host_id), so every line in that round stays in a
# single voice even though the pick is random.
_SERVANT_HOSTS = {
    "easy": "hokusai",
    "medium": "davinci",
    "hard": "vangogh",
    "lunatic": "vritra",
}

# The voice game draws a random host from this pool each round.
_AUDIO_HOSTS = [
    "salieri",
    "nero",
    "mozart",
    "elisabeth",
    "okuni",
    "shakespeare",
    "gilgamesh_caster",
]

_PORTRAITS: dict[str, str] = {}


def host_for(game_type: str, difficulty: str | None = None) -> str:
    if game_type == "guess_audio":
        return random.choice(_AUDIO_HOSTS)
    if game_type == "guess_servant":
        return _SERVANT_HOSTS.get(difficulty or "", "hokusai")
    return "hokusai"


def resolve_portraits(index) -> None:
    """Cache each host's Atlas face URL from the loaded servant index (call once)."""
    for host_id, host in HOSTS.items():
        servant = index.get(host["servant_id"])
        face = getattr(servant, "face", None) if servant else None
        if face:
            _PORTRAITS[host_id] = face


def portrait(host_id: str) -> str | None:
    return _PORTRAITS.get(host_id)


def name(host_id: str) -> str:
    return HOSTS.get(host_id, HOSTS["hokusai"])["name"]


def line(host_id: str, event: str, *, player: str | None = None, answer: str | None = None) -> str:
    pools = HOSTS.get(host_id, HOSTS["hokusai"])["lines"]
    text = random.choice(pools[event])
    if player is not None:
        text = text.replace("{player}", player)
    if answer is not None:
        text = text.replace("{answer}", answer)
    return text
