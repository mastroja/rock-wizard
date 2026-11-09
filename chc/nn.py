"""Convolutional + recurrent note-onset network shared by training and the app."""
import numpy as np
import torch
from torch import nn

from . import features

DOWN = 4  # the recurrent layer runs at 1/4 of the frame rate (train --down picks another)

# output channels per instrument; guitar/bass add a hammer-on flag (at the note) and a
# sustain-held channel (on while a sustain rings) after the five lanes
CHANNELS = {
    "guitar": ["G", "R", "Y", "B", "O", "hopo", "sus"],
    "bass": ["G", "R", "Y", "B", "O", "hopo", "sus"],
    "drums": ["K", "S", "Ycym", "Ytom", "Bcym", "Btom", "Gcym", "Gtom"],
}
CHANNELS["mix"] = ["beat", "down"]
for _k in ("guitar2", "guitar_hr"):
    CHANNELS[_k] = CHANNELS["guitar"]
for _k in ("bass2", "bass_hr"):
    CHANNELS[_k] = CHANNELS["bass"]
CHANNELS["drums_hq"] = CHANNELS["drums"]
CHANNELS["guitar_st"] = CHANNELS["guitar"]
# optional extra outputs (train --rel): is this note's lowest fret above, equal to or below
# the previous note's? Charters place frets by the melody's shape, not absolute pitch.
REL = ["up", "same", "down"]
# optional extra outputs after those (train --size): how many frets the note has (1, 2, 3+);
# chord size was the biggest guitar error when chords were picked from the lane outputs alone
SIZE = ["1 fret", "2 frets", "3 frets"]
# optional last output (train --onset, after those): a note starts here, whatever its frets.
# Note times otherwise come from the strongest lane, which blurs in fast runs that change lanes.
ONSET = ["onset"]


def instrument(kind: str) -> str:
    """Feature/model kind -> instrument ('guitar_hr' -> 'guitar', 'bass2' -> 'bass')."""
    return kind.split("_")[0].rstrip("2")


def n_lanes(kind: str) -> int:
    """Number of note-onset channels (the rest are per-note attributes)."""
    return 5 if instrument(kind) in ("guitar", "bass") else len(CHANNELS[kind])


class NoteNet(nn.Module):
    def __init__(self, kind: str, hidden: int = 128, n_out: int | None = None, context: bool = False,
                 down: int = DOWN, layers: int = 0, wide: int = 1):
        """context: also take the whole song's feature profile (features.song_profile) next to
        every window, so the model knows where the music sits in the song's range.
        down: the recurrent layer runs at 1/down of the frame rate.
        layers: self-attention layers after the recurrent one, so any moment can look at similar
        moments anywhere in the window (the same riff earlier in the song), however far away.
        wide: multiplies the convolution filters."""
        super().__init__()
        self.kind = kind
        self.down = down
        self.layers = layers
        self.n_out = n_out or len(CHANNELS[kind])
        self.context = context
        n_bins = features.n_bins(kind)
        self.in_ch = features.channels(kind)  # stacked signals (stereo guitar: mid, side, keys)
        ch = [self.in_ch, 16 * wide, 32 * wide, 48 * wide]
        pools = [3, 3, 2]
        blocks, f = [], n_bins
        for i in range(3):
            blocks += [nn.Conv2d(ch[i], ch[i + 1], 3, padding=1), nn.BatchNorm2d(ch[i + 1]), nn.ReLU(),
                       nn.Conv2d(ch[i + 1], ch[i + 1], 3, padding=1), nn.BatchNorm2d(ch[i + 1]), nn.ReLU(),
                       nn.MaxPool2d((1, pools[i]))]
            f //= pools[i]
        self.conv = nn.Sequential(*blocks)
        width = 128 * (wide + 1)
        self.proj = nn.Sequential(nn.Linear(ch[-1] * f, width), nn.ReLU(), nn.Dropout(0.2))
        if context:
            self.ctx = nn.Sequential(nn.Linear(2 * n_bins * self.in_ch, 64), nn.ReLU())
            width += 64
        self.rnn = nn.GRU(width, hidden, num_layers=2, batch_first=True, bidirectional=True, dropout=0.2)
        if layers:
            one = nn.TransformerEncoderLayer(2 * hidden, 8, 4 * hidden, dropout=0.1, activation="gelu",
                                             batch_first=True, norm_first=True)
            self.attn = nn.TransformerEncoder(one, layers, norm=nn.LayerNorm(2 * hidden), enable_nested_tensor=False)
        self.out = nn.Linear(2 * hidden + width, self.n_out)

    def forward(self, x, profile=None):  # x: (B, T, F) normalised features; profile: (B, 2F)
        if self.in_ch > 1:  # (B, T, ch*F) -> (B, ch, T, F)
            B, T, _ = x.shape
            x = x.view(B, T, self.in_ch, -1).permute(0, 2, 1, 3)
        else:
            x = x.unsqueeze(1)
        h = self.conv(x)                                   # (B, C, T, F')
        h = h.permute(0, 2, 1, 3).flatten(2)               # (B, T, C*F')
        h = self.proj(h)                                   # full time resolution
        if self.context:
            c = self.ctx(profile)[:, None, :].expand(-1, h.shape[1], -1)
            h = torch.cat([h, c], dim=-1)
        T = h.shape[1]
        pad = (-T) % self.down
        hp = nn.functional.pad(h, (0, 0, 0, pad))
        ctx = nn.functional.avg_pool1d(hp.transpose(1, 2), self.down).transpose(1, 2)
        ctx, _ = self.rnn(ctx)                             # long-range context at 1/down rate
        if self.layers:
            ctx = self.attn(ctx)
        ctx = ctx.repeat_interleave(self.down, dim=1)[:, :T]
        return self.out(torch.cat([ctx, h], dim=-1))       # logits (B, T, n_out)


@torch.no_grad()
def predict(model: NoteNet, feats: np.ndarray, device="cuda", chunk=4096, pad=256) -> np.ndarray:
    """Probabilities (T, n_out) for a whole song, processed in overlapping chunks."""
    model.eval()
    if model.layers:  # a chunk's edges should see as far as its middle does
        pad = 1024
    xn = features.normalize(feats)
    x = torch.from_numpy(xn).to(device)
    prof = torch.from_numpy(features.song_profile(xn))[None].to(device) if model.context else None
    T = x.shape[0]
    out = np.zeros((T, model.n_out), np.float32)
    start = 0
    while start < T:
        a, b = max(0, start - pad), min(T, start + chunk + pad)
        p = torch.sigmoid(model(x[a:b][None], prof))[0].cpu().numpy()
        out[start:min(T, start + chunk)] = p[start - a: start - a + min(chunk, T - start)]
        start += chunk
    return out


def pick(prob: np.ndarray, thresholds, min_gap_frames=3) -> list[tuple[int, int, float]]:
    """Local maxima above per-channel threshold -> [(frame, channel, prob)] sorted by frame."""
    hits = []
    for c in range(prob.shape[1]):
        p = prob[:, c]
        thr = thresholds[c] if hasattr(thresholds, "__len__") else thresholds
        idx = np.where((p[1:-1] >= p[:-2]) & (p[1:-1] > p[2:]) & (p[1:-1] >= thr))[0] + 1
        last = -10 ** 9
        for i in idx:
            if i - last >= min_gap_frames:
                hits.append((int(i), c, float(p[i])))
                last = i
            elif hits and p[i] > hits[-1][2] and hits[-1][1] == c:
                hits[-1] = (int(i), c, float(p[i]))
                last = i
    return sorted(hits)
