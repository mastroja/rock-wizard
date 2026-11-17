"""Songs worth charting, in a new batch every week: this week's iTunes charts for each group
(garage, surf, funk, psych, indie, big riffs, sing-alongs), more by bands already in the library,
and songs picked by hand for how they play: one clear guitar part, a bass line you can follow,
drums that drive, or a vocal people want to sing. Songs not suggested before come first. Nothing
here is measured; the Create page searches for a pick the normal way, so a human chart still wins
when there is one."""
import json
import random
import re
import threading
import time
import traceback
import urllib.parse
from collections import Counter
from pathlib import Path

from . import library, samples, search

GARAGE, FUNK, PSYCH, INDIE, RIFFS, SING = ("Garage & surf", "Funk & bass", "Psych", "Indie & post-punk", "Big riffs",
                                           "Sing-alongs")
PICKS = [
    (GARAGE, "Thee Oh Sees", "The Dream", "Fast, fuzzy, relentless strumming"),
    (GARAGE, "Ty Segall", "Girlfriend", "Short fuzz-punk with one loud guitar"),
    (GARAGE, "Wavves", "King of the Beach", "Surf-punk chords and a big chorus"),
    (GARAGE, "FIDLAR", "Cheap Beer", "Fast skate-punk that's easy to sing"),
    (GARAGE, "The Chats", "Smoko", "Two minutes of pure pub punk"),
    (GARAGE, "DZ Deathrays", "Gina Works at Hearts", "Thrashy Aussie riffs"),
    (GARAGE, "Dick Dale", "Misirlou", "The ultimate tremolo picking run"),
    (GARAGE, "Surfer Blood", "Swim", "One huge reverb hook"),
    (GARAGE, "Black Lips", "Bad Kids", "Scrappy garage sing-along"),
    (GARAGE, "The Hives", "Hate to Say I Told You So", "Tight riff with stop-start drums"),
    (GARAGE, "The Vines", "Get Free", "Short, loud and all chorus"),
    (GARAGE, "The Datsuns", "In Love", "Straight-ahead garage boogie"),
    (GARAGE, "The Stooges", "I Wanna Be Your Dog", "Three fuzz chords, hammered home"),
    (GARAGE, "The Sonics", "Have Love, Will Travel", "Raw sixties garage with a riff to match"),
    (GARAGE, "The Ventures", "Walk, Don't Run", "Clean surf lead, all melody"),
    (GARAGE, "Jay Reatard", "My Shadow", "Breakneck punk strumming"),
    (GARAGE, "The Growlers", "One Million Lovers", "Loose, woozy beach rock"),
    (GARAGE, "Skegss", "Up in the Clouds", "Sunny surf-rock chords"),

    (FUNK, "Vulfpeck", "Dean Town", "A bass line that never lets up"),
    (FUNK, "Khruangbin", "Maria También", "Surfy lead over a locked-in groove"),
    (FUNK, "The Meters", "Cissy Strut", "Sparse funk where every note counts"),
    (FUNK, "Parliament", "Give Up the Funk", "Bouncy bass and a chant to shout"),
    (FUNK, "Stevie Wonder", "Superstition", "One of the great riffs, with busy drums"),
    (FUNK, "Rick James", "Super Freak", "A bass hook everyone knows"),
    (FUNK, "Chic", "Good Times", "The bass line half of pop borrowed"),
    (FUNK, "Thundercat", "Them Changes", "Slinky, singing bass"),
    (FUNK, "Jamiroquai", "Virtual Insanity", "Sliding bass under a huge vocal"),
    (FUNK, "Kool & the Gang", "Jungle Boogie", "Fat, simple groove"),
    (FUNK, "Bill Withers", "Use Me", "Tight groove with a talk-sung vocal"),
    (FUNK, "Talking Heads", "Psycho Killer", "A bass riff that carries the song"),
    (FUNK, "Cake", "Short Skirt/Long Jacket", "Bass up front, and fun to talk-sing"),
    (FUNK, "Primus", "Jerry Was a Race Car Driver", "A real bass workout"),
    (FUNK, "Sly & the Family Stone", "Thank You (Falettinme Be Mice Elf Agin)", "Where slap bass started"),
    (FUNK, "James Brown", "Get Up (I Feel Like Being a) Sex Machine", "One groove, locked in tight"),
    (FUNK, "Earth, Wind & Fire", "September", "Bouncing bass and a chorus for everyone"),
    (FUNK, "Daft Punk", "Get Lucky", "Chopping funk guitar that never stops"),
    (FUNK, "Vulfpeck", "Back Pocket", "Tight, playful and bass-led"),
    (FUNK, "Red Hot Chili Peppers", "Can't Stop", "Choppy riff over a busy bass"),

    (PSYCH, "Tame Impala", "Elephant", "Stomping fuzz riff"),
    (PSYCH, "Tame Impala", "Let It Happen", "A long groove for bass and drums"),
    (PSYCH, "King Gizzard & the Lizard Wizard", "Robot Stop", "Relentless motorik riffing"),
    (PSYCH, "King Gizzard & the Lizard Wizard", "Rattlesnake", "Fast, hypnotic, and it never stops"),
    (PSYCH, "Temples", "Shelter Song", "Jangly riff that's easy to follow"),
    (PSYCH, "Allah-Las", "Catamaran", "Laid-back surf psych"),
    (PSYCH, "Unknown Mortal Orchestra", "Multi-Love", "Wonky groove with a great vocal"),
    (PSYCH, "MGMT", "Electric Feel", "Rubbery bass line"),
    (PSYCH, "The Black Angels", "Young Men Dead", "Heavy, droning stomp"),
    (PSYCH, "Pond", "Giant Tortoise", "Big, sludgy psych riff"),
    (PSYCH, "The Flaming Lips", "Race for the Prize", "Pounding drums and a soaring vocal"),
    (PSYCH, "Mild High Club", "Homage", "Woozy, jazzy chords"),
    (PSYCH, "Pink Floyd", "Money", "That bass riff in seven"),
    (PSYCH, "The Doors", "Break On Through (To the Other Side)", "Driving bossa beat and a wild vocal"),
    (PSYCH, "Jimi Hendrix", "Purple Haze", "The fuzz riff that started it all"),
    (PSYCH, "Tame Impala", "The Less I Know the Better", "A bass line that is the whole song"),
    (PSYCH, "The Brian Jonestown Massacre", "Anemone", "Slow, hazy and hypnotic"),
    (PSYCH, "Psychedelic Porn Crumpets", "Found God in a Tomato", "Fast riffs that keep changing"),

    (INDIE, "Arctic Monkeys", "Brianstorm", "Frantic drums from start to finish"),
    (INDIE, "Interpol", "Evil", "Bass-led and easy to sing"),
    (INDIE, "Franz Ferdinand", "Take Me Out", "Riff switch halfway through"),
    (INDIE, "Yeah Yeah Yeahs", "Date with the Night", "Short, wild and noisy"),
    (INDIE, "Car Seat Headrest", "Fill in the Blank", "Builds to a big shout-along"),
    (INDIE, "Wolf Alice", "Yuk Foo", "Two minutes of pure fury"),
    (INDIE, "Courtney Barnett", "Pedestrian at Best", "Loose garage riff with a wordy vocal"),
    (INDIE, "Viagra Boys", "Sports", "Deadpan vocal over a fat bass line"),
    (INDIE, "IDLES", "Danny Nedelko", "Punchy and made to be shouted"),
    (INDIE, "Fontaines D.C.", "Boys in the Better Land", "Driving post-punk"),
    (INDIE, "Bloc Party", "Helicopter", "Busy, precise drumming"),
    (INDIE, "The Strokes", "Reptilia", "Clean riffs and a killer solo"),
    (INDIE, "Parquet Courts", "Stoned and Starving", "Long, wiry guitar jam"),
    (INDIE, "Foals", "Inhaler", "Quiet verses, then a massive riff"),
    (INDIE, "Pixies", "Where Is My Mind?", "Simple lead line everyone knows"),
    (INDIE, "Joy Division", "Disorder", "Bass up front and racing drums"),
    (INDIE, "The Smiths", "This Charming Man", "Jangling guitar that never sits still"),
    (INDIE, "Modest Mouse", "Float On", "Bouncy riff and a shout-along chorus"),
    (INDIE, "Vampire Weekend", "A-Punk", "Two minutes of quick, clean picking"),
    (INDIE, "The Cure", "Just Like Heaven", "Bright lead guitar and a bass you can hum"),
    (INDIE, "LCD Soundsystem", "Daft Punk Is Playing at My House", "Cowbell, bass and shouting"),

    (RIFFS, "Audioslave", "Cochise", "Monster opening riff"),
    (RIFFS, "Soundgarden", "Spoonman", "Odd meters that still groove"),
    (RIFFS, "Royal Blood", "Figure It Out", "Just bass and drums, and it's huge"),
    (RIFFS, "Muse", "Hysteria", "A bass line that's a real workout"),
    (RIFFS, "Queens of the Stone Age", "Go with the Flow", "Driving and hypnotic"),
    (RIFFS, "Kyuss", "Green Machine", "Fuzz riff with a groove"),
    (RIFFS, "Wolfmother", "Woman", "Seventies riffs turned up"),
    (RIFFS, "Ghost", "Square Hammer", "Catchy metal with a big chorus"),
    (RIFFS, "Tool", "Schism", "Tricky meters for bass and drums"),
    (RIFFS, "Cream", "Sunshine of Your Love", "A classic riff anyone can follow"),
    (RIFFS, "Led Zeppelin", "When the Levee Breaks", "The biggest drum sound there is"),
    (RIFFS, "The White Stripes", "Seven Nation Army", "The riff everybody knows"),
    (RIFFS, "The Black Keys", "Lonely Boy", "Short, fast and fun"),
    (RIFFS, "Turnstile", "Mystery", "Hardcore bounce with a hook"),
    (RIFFS, "Gojira", "Stranded", "Crushing and rhythmic"),
    (RIFFS, "Deftones", "My Own Summer (Shove It)", "Heavy, simple and tight"),
    (RIFFS, "Rage Against the Machine", "Killing in the Name", "Huge riffs and a build to shout"),
    (RIFFS, "Black Sabbath", "Paranoid", "Fast, simple and heavy"),
    (RIFFS, "AC/DC", "Back in Black", "The riff every guitarist learns"),
    (RIFFS, "Mastodon", "Blood and Thunder", "Relentless and full of drum fills"),
    (RIFFS, "System of a Down", "Chop Suey!", "Stop-start chaos with a big chorus"),
    (RIFFS, "Nirvana", "Breed", "Flat-out from the first bar"),
    (RIFFS, "Foo Fighters", "Everlong", "Driving chords and non-stop drums"),

    (SING, "The Killers", "Mr. Brightside", "Everyone knows every word"),
    (SING, "Weezer", "Buddy Holly", "Simple power chords and a great melody"),
    (SING, "Oasis", "Don't Look Back in Anger", "Built for singing along"),
    (SING, "Journey", "Don't Stop Believin'", "The karaoke champion"),
    (SING, "Fleetwood Mac", "Go Your Own Way", "Big harmonies and a great solo"),
    (SING, "Paramore", "Misery Business", "Fast and a real vocal challenge"),
    (SING, "Bon Jovi", "Livin' on a Prayer", "That key change"),
    (SING, "Tom Petty", "Free Fallin'", "Easy, warm and singable"),
    (SING, "My Chemical Romance", "Welcome to the Black Parade", "A huge build with a huge payoff"),
    (SING, "The Cranberries", "Zombie", "Massive chorus and a simple riff"),
    (SING, "Alanis Morissette", "You Oughta Know", "Great bass and a vocal to belt"),
    (SING, "Sublime", "Santeria", "Laid-back groove, easy to sing"),
    (SING, "Third Eye Blind", "Semi-Charmed Life", "Fast words and a catchy riff"),
    (SING, "Queen", "Don't Stop Me Now", "Pure joy, with a great solo"),
    (SING, "blink-182", "All the Small Things", "Three chords and a chorus to yell"),
    (SING, "Green Day", "Basket Case", "Fast pop-punk everyone knows"),
    (SING, "The Proclaimers", "I'm Gonna Be (500 Miles)", "Impossible not to sing"),
    (SING, "Semisonic", "Closing Time", "Easy chords and a big last chorus"),
    (SING, "Smash Mouth", "All Star", "Everyone knows every word of this one too"),
    (SING, "No Doubt", "Just a Girl", "Bouncy riff and a vocal with attitude"),
]


PICKS += [
    (GARAGE, "The Sonics", "Psycho", "Pounding drums and a screamed vocal"),
    (GARAGE, "Ramones", "Blitzkrieg Bop", "Downstrokes all the way"),
    (GARAGE, "The Cramps", "Human Fly", "Twangy, creepy rockabilly"),
    (GARAGE, "Dead Kennedys", "Holiday in Cambodia", "A surf lead that turns into a punk stomp"),
    (GARAGE, "The Trashmen", "Surfin' Bird", "Two chords and a lot of noise"),
    (GARAGE, "Agent Orange", "Bloodstains", "Surf-punk at full speed"),
    (GARAGE, "Amyl and the Sniffers", "Hertz", "Pub-punk with a vocal full of attitude"),
    (GARAGE, "The Undertones", "Teenage Kicks", "Perfect two-minute punk-pop"),
    (GARAGE, "Mudhoney", "Touch Me I'm Sick", "Filthy fuzz, played fast"),
    (GARAGE, "White Reaper", "Might Be Right", "Twin leads and a big chorus"),
    (GARAGE, "Jet", "Are You Gonna Be My Girl", "That bass intro, then a riot"),
    (GARAGE, "The Hives", "Main Offender", "Tight, stabbing chords"),
    (GARAGE, "Thee Oh Sees", "Toe Cutter - Thumb Buster", "A wiry riff that keeps going"),
    (GARAGE, "The Clash", "I Fought the Law", "Clean punk chords and a chorus for everyone"),
    (GARAGE, "The Ventures", "Pipeline", "Low surf twang, all melody"),
    (GARAGE, "Bad Religion", "21st Century (Digital Boy)", "Fast punk with real harmonies"),

    (FUNK, "Chic", "Le Freak", "Choppy guitar over a bouncing bass"),
    (FUNK, "Mark Ronson", "Uptown Funk", "Horn stabs and a groove everyone knows"),
    (FUNK, "Michael Jackson", "Billie Jean", "One of the most famous bass lines there is"),
    (FUNK, "Queen", "Another One Bites the Dust", "The bass riff is the song"),
    (FUNK, "Herbie Hancock", "Chameleon", "A long synth-bass groove"),
    (FUNK, "Tower of Power", "What Is Hip?", "Busy sixteenths for bass and drums"),
    (FUNK, "The Meters", "Look-Ka Py Py", "Choppy, syncopated and tight"),
    (FUNK, "Ohio Players", "Fire", "Sirens, slap bass and a big chorus"),
    (FUNK, "Average White Band", "Pick Up the Pieces", "An instrumental groove that never quits"),
    (FUNK, "Wild Cherry", "Play That Funky Music", "Simple and endlessly fun"),
    (FUNK, "Lettuce", "Squadlive", "Fat drums and a locked-in bass"),
    (FUNK, "Cory Wong", "Golden", "Clean, fast rhythm guitar"),
    (FUNK, "Prince", "Kiss", "Bare-bones funk guitar and a falsetto"),
    (FUNK, "Red Hot Chili Peppers", "Give It Away", "Slap bass from start to finish"),
    (FUNK, "INXS", "Need You Tonight", "A riff that never moves, and doesn't need to"),

    (PSYCH, "Jefferson Airplane", "White Rabbit", "A slow build to a huge finish"),
    (PSYCH, "Cream", "White Room", "Wah guitar and a big drum part"),
    (PSYCH, "Iron Butterfly", "In-A-Gadda-Da-Vida", "A riff anyone can play, and a drum solo"),
    (PSYCH, "Steppenwolf", "Magic Carpet Ride", "Easy chords and a groove for drums"),
    (PSYCH, "The Zombies", "Time of the Season", "Bass and handclaps carry it"),
    (PSYCH, "The 13th Floor Elevators", "You're Gonna Miss Me", "Raw garage psych with a wild vocal"),
    (PSYCH, "Love", "7 and 7 Is", "Frantic drums from the first beat"),
    (PSYCH, "Blue Cheer", "Summertime Blues", "Loud, sludgy and simple"),
    (PSYCH, "The Byrds", "Eight Miles High", "Jangly twelve-string leads"),
    (PSYCH, "Wolfmother", "Joker and the Thief", "Galloping riff, organ and all"),
    (PSYCH, "Khruangbin", "Time (You and I)", "Disco groove with a surfy lead"),
    (PSYCH, "Tame Impala", "Mind Mischief", "Dreamy chords over a heavy beat"),
    (PSYCH, "King Gizzard & the Lizard Wizard", "Work This Time", "Mellow and catchy"),
    (PSYCH, "Ty Segall", "Feel", "Fuzz duel with a bongo groove"),
    (PSYCH, "The Black Angels", "Entrance Song", "Pounding, hypnotic drums"),
    (PSYCH, "Deep Purple", "Hush", "Organ, a big beat and an easy chorus"),

    (INDIE, "Arctic Monkeys", "I Bet You Look Good on the Dancefloor", "Fast, tight and danceable"),
    (INDIE, "Yeah Yeah Yeahs", "Maps", "A simple riff and a heartfelt vocal"),
    (INDIE, "Pixies", "Debaser", "Bass up front and a scream to match"),
    (INDIE, "Joy Division", "Love Will Tear Us Apart", "Bass melody and driving drums"),
    (INDIE, "Bloc Party", "Banquet", "Choppy riffs and a racing beat"),
    (INDIE, "Pavement", "Cut Your Hair", "Slack, catchy and fun to sing"),
    (INDIE, "Gang of Four", "Damaged Goods", "Jagged guitar over a funky bass"),
    (INDIE, "Wet Leg", "Chaise Longue", "Deadpan vocals over a punchy riff"),
    (INDIE, "The Libertines", "Don't Look Back into the Sun", "Ramshackle and full of energy"),
    (INDIE, "Kings of Leon", "Molly's Chambers", "Garage riff with a growled vocal"),
    (INDIE, "Television", "See No Evil", "Interlocking guitars"),
    (INDIE, "The White Stripes", "Fell in Love with a Girl", "Two minutes of fuzz and bash"),
    (INDIE, "Interpol", "Slow Hands", "A bass that drives everything"),
    (INDIE, "The Killers", "Somebody Told Me", "Bass-led and very fast"),
    (INDIE, "Bloc Party", "Like Eating Glass", "Tom-heavy drums the whole way"),
    (INDIE, "Weezer", "Say It Ain't So", "Quiet verse, crushing chorus"),
    (INDIE, "Big Thief", "Not", "A long build to a noisy solo"),

    (RIFFS, "Queens of the Stone Age", "No One Knows", "Stop-start riff and great drum fills"),
    (RIFFS, "Metallica", "Master of Puppets", "Fast downpicking for days"),
    (RIFFS, "Iron Maiden", "The Trooper", "Galloping bass and twin leads"),
    (RIFFS, "Van Halen", "Ain't Talkin' 'bout Love", "A simple riff with a lot of swagger"),
    (RIFFS, "Pantera", "Walk", "A heavy, swinging groove"),
    (RIFFS, "Rush", "Tom Sawyer", "Tricky parts for all three players"),
    (RIFFS, "Alice in Chains", "Man in the Box", "Sludgy riff with a talk-box hook"),
    (RIFFS, "Slayer", "Raining Blood", "Fast, heavy and precise"),
    (RIFFS, "Megadeth", "Symphony of Destruction", "Mid-tempo and very tight"),
    (RIFFS, "Judas Priest", "Breaking the Law", "One of the easiest metal riffs to learn"),
    (RIFFS, "Motörhead", "Ace of Spades", "Full throttle all the way"),
    (RIFFS, "Thin Lizzy", "The Boys Are Back in Town", "Twin guitar harmonies"),
    (RIFFS, "Led Zeppelin", "Immigrant Song", "Galloping riff and a famous wail"),
    (RIFFS, "Black Sabbath", "Iron Man", "Slow, heavy and iconic"),
    (RIFFS, "Sleep", "Dragonaut", "Huge, slow fuzz"),
    (RIFFS, "Avenged Sevenfold", "Bat Country", "Big solos and a big chorus"),
    (RIFFS, "Muse", "Knights of Cydonia", "A gallop that builds and builds"),

    (SING, "The Darkness", "I Believe in a Thing Called Love", "Falsetto and riffs galore"),
    (SING, "Twisted Sister", "We're Not Gonna Take It", "A chant everyone can join"),
    (SING, "Survivor", "Eye of the Tiger", "That chugging riff"),
    (SING, "Boston", "More Than a Feeling", "Soaring vocal and big guitars"),
    (SING, "Kiss", "Rock and Roll All Nite", "Pure party rock"),
    (SING, "Joan Jett & the Blackhearts", "I Love Rock 'n' Roll", "Three chords and a stomp"),
    (SING, "Pat Benatar", "Hit Me with Your Best Shot", "A riff and a vocal with punch"),
    (SING, "Avril Lavigne", "Sk8er Boi", "Pop-punk built for singing"),
    (SING, "The Offspring", "Self Esteem", "Quiet, loud, and everyone sings"),
    (SING, "Fall Out Boy", "Sugar, We're Goin Down", "Tongue-twister verses and a huge chorus"),
    (SING, "Bryan Adams", "Summer of '69", "Easy chords and a great story"),
    (SING, "Jimmy Eat World", "The Middle", "Bright, simple and uplifting"),
    (SING, "Lit", "My Own Worst Enemy", "Snappy riff and a chorus to yell"),
    (SING, "The Romantics", "What I Like About You", "Fast, fun and catchy"),
    (SING, "Def Leppard", "Pour Some Sugar on Me", "Stomping beat and a shout-along"),
    (SING, "Toto", "Hold the Line", "Piano stabs and a great solo"),
    (SING, "Everclear", "Santa Monica", "Crunchy chords and a sing-along chorus"),
]

YOURS = "From bands you play"
# iTunes's own top songs in a genre (they move week to week): feed id and name, per group
CHARTS = {
    GARAGE: [(1006, "Punk"), (1162, "Surf"), (1157, "Rock & Roll")],
    FUNK: [(1139, "Funk"), (1137, "Disco"), (1140, "Motown")],
    PSYCH: [(1156, "Psychedelic"), (1155, "Prog-Rock")],
    INDIE: [(1004, "Indie Rock"), (1005, "New Wave"), (20, "Alternative")],
    RIFFS: [(1152, "Hard Rock"), (1153, "Metal"), (1003, "Grunge"), (1147, "Blues-Rock")],
    SING: [(21, "Rock"), (1148, "British Invasion"), (1151, "Hair Metal")],
}
PER_GROUP = 12      # songs in each group of a batch
FROM_CHARTS = 6     # at most this many of them from this week's charts
ARTISTS = 10        # library artists looked up each batch, for YOURS
RETRY = 6 * 3600    # a batch made without the network is tried again after this long
FILE = library.config_path().with_name(".rockwizard_suggest.json")
EXTRA_TAG = re.compile(r"\s*[\(\[][^\)\]]*\b(feat|ft|from|remaster\w*|version|edit|mix|mono|stereo|bonus|single)\b[^\)\]]*[\)\]]"
                       r"|\s+-\s+[^-]*\b(remaster\w*|version|edit|mix|mono|stereo|single)(\s+\d{4})?\s*$", re.I)
FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)
_lock = threading.Lock()
_state: dict | None = None
_building = False


def _week() -> str:
    """This week, Monday to Sunday: '2026-W47'."""
    return time.strftime("%G-W%V")


def _load() -> dict:
    global _state
    if _state is None:
        try:
            _state = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _state = {}
        _state.setdefault("n", 0)
        _state.setdefault("shown", {})  # song key -> the batch number it was last in
        _state.setdefault("asked", {})  # library artist -> the batch number they were last looked up in
        _state.setdefault("songs", [])
    return _state


def _save() -> None:
    try:
        FILE.write_text(json.dumps(_state), encoding="utf-8")
    except OSError:
        pass


def _title(name: str) -> str:
    """'Song (feat. X) [From Y]' or 'Song (2012 Remaster)' -> 'Song'."""
    return EXTRA_TAG.sub("", name).strip() or name


def _chart(gid: int) -> list[dict]:
    """A genre's top songs on iTunes, best first, with their covers and preview clips."""
    feed = samples.fetch(f"https://itunes.apple.com/us/rss/topsongs/limit=50/genre={gid}/json").get("feed", {})
    entries = feed.get("entry") or []
    out = []
    for rank, e in enumerate([entries] if isinstance(entries, dict) else entries, 1):
        name, by = e.get("im:name", {}).get("label", ""), e.get("im:artist", {}).get("label", "")
        if not name or not by or search.OTHER_TAKE.search(name):
            continue  # live takes, remixes, re-recordings and covers
        links = e.get("link") or []
        audio = next((x["attributes"]["href"] for x in ([links] if isinstance(links, dict) else links)
                      if x.get("attributes", {}).get("im:assetType") == "preview"), "")
        imgs = e.get("im:image") or []
        art = imgs[-1]["label"].replace("170x170bb", "200x200bb") if imgs else ""
        out.append({"artist": FEAT.sub("", by).strip() or by, "title": _title(name), "rank": rank}
                   | ({"art": art, "audio": audio} if art and audio else {}))
    return out


def _more_by(artist: str, owned: set, most: int = 2) -> list[dict]:
    """Songs by an artist that aren't in the library yet, from iTunes (its best known first)."""
    q = urllib.parse.urlencode({"term": artist, "entity": "song", "attribute": "artistTerm", "limit": 40})
    out, seen = [], set()
    for it in samples.fetch(f"https://itunes.apple.com/search?{q}").get("results", []):
        name = it.get("trackName") or ""
        if search.normalize(search.lead_artist(it.get("artistName") or "")) != search.normalize(artist):
            continue
        if search.OTHER_TAKE.search(f"{name} {it.get('collectionName') or ''}"):
            continue
        t = _title(name)
        k = library.song_key(artist, t)
        if k in owned or k in seen:
            continue
        seen.add(k)
        art = (it.get("artworkUrl100") or "").replace("100x100bb", "200x200bb")
        out.append({"artist": artist, "title": t} | ({"art": art, "audio": it["previewUrl"]} if art and it.get("previewUrl") else {}))
        if len(out) >= most:
            break
    return out


def _make(owned: set, artists: Counter, network: bool) -> None:
    """A new batch: for each group, songs on this week's iTunes charts and hand picks, the ones
    never suggested before first (then the longest ago); and more by bands already in the library.
    Without the network (or when iTunes can't be reached) it's hand picks only."""
    with _lock:
        st = _load()
        n, shown, asked = st["n"] + 1, dict(st["shown"]), dict(st["asked"])
    age = lambda s: shown.get(s["key"], -1)
    rnd, used, batch, reached = random.Random(), set(owned), [], False
    titles = set()  # the same song spelt differently (Dick Dale vs Dick Dale & His Del-Tones) only once

    def take(rows, most, artists_in):
        got = []
        for s in rows:
            if len(got) >= most:
                break
            t = search.normalize(s["title"])
            if s["key"] in used or t in titles or search.normalize(s["artist"]) in artists_in:
                continue
            used.add(s["key"]); titles.add(t); artists_in.add(search.normalize(s["artist"])); got.append(s)
        return got

    if network and artists:  # more by the bands you play: the ones not looked up lately first
        names = sorted(artists, key=lambda a: (asked.get(a, -1), -artists[a] * rnd.random()))[:ARTISTS]
        mine, seen_artists = [], set()
        for a in names:
            try:
                found = _more_by(a, used)
                reached = True
            except Exception:
                continue
            asked[a] = n
            c = artists[a]
            mine += [s | {"group": YOURS, "key": library.song_key(s["artist"], s["title"]),
                          "why": f"You have {c} song{'s' if c != 1 else ''} by them"} for s in found]
        mine.sort(key=age)
        batch += take(mine, PER_GROUP, seen_artists)
    for group, feeds in CHARTS.items():
        charted = []
        for gid, label in feeds if network else []:
            try:
                charted += [s | {"group": group, "key": library.song_key(s["artist"], s["title"]),
                                 "why": f"#{s['rank']} on the iTunes {label} chart this week"} for s in _chart(gid)]
                reached = True
            except Exception:
                pass
        charted.sort(key=lambda s: (age(s), s["rank"]))
        hand = [{"group": g, "artist": a, "title": t, "why": why, "key": library.song_key(a, t)} for g, a, t, why in PICKS if g == group]
        rnd.shuffle(hand)
        hand.sort(key=age)  # a stable sort: shuffled within each age
        in_group = set()
        got = take(charted, FROM_CHARTS, in_group)
        got += take(hand, PER_GROUP - len(got), in_group)
        got += take(charted, PER_GROUP - len(got), in_group)  # few hand picks left: more from the charts
        rnd.shuffle(got)
        batch += got
    with _lock:
        st = _load()
        for s in batch:
            st["shown"][s["key"]] = n
        st["asked"].update(asked)
        st.update(n=n, week=_week(), made=time.time(), songs=batch, partial=not reached)
        _save()


def refresh(owned: set, artists: Counter) -> bool:
    """Make a new batch in the background (it asks iTunes a few dozen times, spaced out, so it
    takes a minute or two). False when one is already being made."""
    global _building
    with _lock:
        if _building:
            return False
        _building = True

    def run():
        global _building
        try:
            _make(owned, artists, network=True)
        except Exception:
            traceback.print_exc()
        finally:
            _building = False
    threading.Thread(target=run, daemon=True).start()
    return True


def current(owned: set, artists: Counter) -> dict:
    """This week's batch, less anything added to the library since. A new week (or a batch made
    offline a while ago) starts a new one in the background; until it's ready the last one shows."""
    with _lock:
        st = _load()
        first = not st["songs"]
    if first:  # the very first time: hand picks straight away, while the full batch is made
        _make(owned, artists, network=False)
        with _lock:
            st["shown"] = {}  # those don't count as shown
    if first or st.get("week") != _week() or (st.get("partial") and time.time() - st.get("made", 0) > RETRY):
        refresh(owned, artists)
    with _lock:
        return {"songs": [s for s in st["songs"] if s["key"] not in owned], "week": st.get("week", ""),
                "made": st.get("made", 0), "building": _building}
