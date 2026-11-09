"""Songs worth charting, picked by hand for how they play: one clear guitar part, a bass line you
can follow, drums that drive, or a vocal people want to sing. Leans toward what the library
already has (garage, surf, funk, psych, indie, big riffs). Nothing here is measured; the Create
page searches for a pick the normal way, so a human chart still wins when there is one."""
from pathlib import Path

from . import library

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
]


def picks(*roots: Path | None) -> list[dict]:
    """Every pick that isn't already in these song folders."""
    owned = {library.song_key(*library.names_of(f)) for r in roots if r and Path(r).exists() for f in library.song_folders(r)}
    return [{"group": g, "artist": a, "title": t, "why": why} for g, a, t, why in PICKS
            if library.song_key(a, t) not in owned]
