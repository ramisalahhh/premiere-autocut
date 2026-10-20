/*  AutoCut - ExtendScript side (Premiere Pro 2022+)
    ES3 only: no JSON object, no let/const, no arrow functions.            */

var AutoCut = AutoCut || {};

AutoCut.str = function (v) {
    if (v === null || v === undefined) return "null";
    var t = typeof v;
    if (t === "number") return isFinite(v) ? String(v) : "null";
    if (t === "boolean") return v ? "true" : "false";
    if (t === "string") {
        return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
            .replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t") + '"';
    }
    var i, parts = [];
    if (v instanceof Array) {
        for (i = 0; i < v.length; i++) parts.push(AutoCut.str(v[i]));
        return "[" + parts.join(",") + "]";
    }
    for (var k in v) {
        if (v.hasOwnProperty(k)) parts.push(AutoCut.str(String(k)) + ":" + AutoCut.str(v[k]));
    }
    return "{" + parts.join(",") + "}";
};

AutoCut.ok = function (o) { o = o || {}; o.ok = true; return AutoCut.str(o); };
AutoCut.err = function (msg) { return AutoCut.str({ ok: false, error: String(msg) }); };

AutoCut.ping = function () { return AutoCut.ok({ version: app.version }); };

AutoCut.findSequence = function (id) {
    var active = app.project.activeSequence;
    if (active && (!id || active.sequenceID === id)) return active;
    for (var i = 0; i < app.project.sequences.numSequences; i++) {
        if (app.project.sequences[i].sequenceID === id) return app.project.sequences[i];
    }
    return null;
};

AutoCut.findItem = function (item, nodeId) {
    if (!item) return null;
    if (item.nodeId === nodeId) return item;
    if (item.children) {
        for (var i = 0; i < item.children.numItems; i++) {
            var r = AutoCut.findItem(item.children[i], nodeId);
            if (r) return r;
        }
    }
    return null;
};

/* Read the clips on V1 of the active sequence (the talking-head track). */
AutoCut.getClips = function () {
    try {
        var seq = app.project.activeSequence;
        if (!seq) return AutoCut.err("Open the sequence you want to clean first.");
        if (seq.videoTracks.numTracks < 1) return AutoCut.err("Sequence has no video tracks.");
        var track = seq.videoTracks[0];
        var clips = [], warnings = [];
        for (var i = 0; i < track.clips.numItems; i++) {
            var c = track.clips[i];
            var pi = c.projectItem;
            if (!pi) { warnings.push("Clip " + (i + 1) + " has no project item - skipped"); continue; }
            var path = "";
            try { path = pi.getMediaPath(); } catch (e1) {}
            if (!path) { warnings.push("'" + c.name + "' is a nest/merged clip/graphic - skipped"); continue; }
            var speed = 1;
            try { speed = c.getSpeed(); } catch (e2) {}
            if (Math.abs(speed - 1) > 0.001) { warnings.push("'" + c.name + "' has speed " + speed + " - skipped"); continue; }
            clips.push({
                index: i, nodeId: pi.nodeId, name: c.name, path: path,
                "in": c.inPoint.seconds, out: c.outPoint.seconds,
                start: c.start.seconds, end: c.end.seconds
            });
        }
        if (!clips.length) return AutoCut.err("No usable clips on V1. " + warnings.join("; "));
        return AutoCut.ok({ seqId: seq.sequenceID, seqName: seq.name, clips: clips, warnings: warnings });
    } catch (e) {
        return AutoCut.err(e.toString() + " (line " + e.line + ")");
    }
};

AutoCut.getOrCreateBin = function (name) {
    var root = app.project.rootItem, i;
    for (i = 0; i < root.children.numItems; i++) {
        var it = root.children[i];
        if (it.type === ProjectItemType.BIN && it.name === name) return it;
    }
    var b = root.createBin(name);
    if (b) return b;
    for (i = 0; i < root.children.numItems; i++) {
        if (root.children[i].type === ProjectItemType.BIN && root.children[i].name === name) return root.children[i];
    }
    return root;
};

AutoCut.T = function (sec) { var t = new Time(); t.seconds = sec; return t; };

AutoCut.pad = function (n) { var s = String(n); while (s.length < 4) s = "0" + s; return s; };

AutoCut.addMarker = function (seq, m) {
    var mk = seq.markers.createMarker(m.t);
    mk.name = m.name;
    mk.comments = m.comment || "";
    if (m.d && m.d > 0) {
        try { mk.end = AutoCut.T(m.t + m.d); } catch (e1) { try { mk.end = m.t + m.d; } catch (e2) {} }
    }
    try { mk.setColorByIndex(m.color); } catch (e3) {}
};

/* Build a NEW sequence from subclips of the kept ranges. Original untouched. */
AutoCut.build = function (p) {
    try {
        var seq = AutoCut.findSequence(p.seqId);
        if (!seq) return AutoCut.err("Original sequence not found.");
        var track = seq.videoTracks[0];
        var bin = AutoCut.getOrCreateBin(p.seqName);
        var subs = [], i;

        for (i = 0; i < p.pieces.length; i++) {
            var pc = p.pieces[i];
            var item = null;
            try {
                var c = track.clips[pc.clipIndex];
                if (c && c.projectItem && c.projectItem.nodeId === pc.nodeId) item = c.projectItem;
            } catch (e0) {}
            if (!item) item = AutoCut.findItem(app.project.rootItem, pc.nodeId);
            if (!item) return AutoCut.err("Source clip not found for piece " + (i + 1) + ". Did the timeline change?");

            var name = "AC_" + AutoCut.pad(i + 1);
            var sub = null;
            try { sub = item.createSubClip(name, AutoCut.T(pc.s), AutoCut.T(pc.e), 0, 1, 1); } catch (e1) {}
            if (!sub) { try { sub = item.createSubClip(name, AutoCut.T(pc.s), AutoCut.T(pc.e), 0, 0, 1); } catch (e2) {} }
            if (!sub) return AutoCut.err("Could not create subclip " + name);
            try { sub.moveBin(bin); } catch (e3) {}
            subs.push(sub);
        }

        var newSeq = app.project.createNewSequenceFromClips(p.seqName, subs, bin);
        if (!newSeq) return AutoCut.err("Premiere refused to create the sequence.");

        var markers = p.markers || [];
        for (i = 0; i < markers.length; i++) {
            try { AutoCut.addMarker(newSeq, markers[i]); } catch (e4) {}
        }
        try { app.project.openSequence(newSeq.sequenceID); } catch (e5) {}
        return AutoCut.ok({ pieces: subs.length, markers: markers.length, name: newSeq.name });
    } catch (e) {
        return AutoCut.err(e.toString() + " (line " + e.line + ")");
    }
};

/* Preview: put markers on the ORIGINAL sequence (nothing is cut). */
AutoCut.addMarkers = function (p) {
    try {
        var seq = AutoCut.findSequence(p.seqId);
        if (!seq) return AutoCut.err("Sequence not found.");
        AutoCut._clear(seq);
        for (var i = 0; i < p.markers.length; i++) {
            try { AutoCut.addMarker(seq, p.markers[i]); } catch (e1) {}
        }
        return AutoCut.ok({ markers: p.markers.length });
    } catch (e) {
        return AutoCut.err(e.toString() + " (line " + e.line + ")");
    }
};

AutoCut._clear = function (seq) {
    var ms = seq.markers, list = [], m = ms.getFirstMarker();
    while (m) {
        if (m.name && m.name.indexOf("AC:") === 0) list.push(m);
        m = ms.getNextMarker(m);
    }
    for (var i = 0; i < list.length; i++) ms.deleteMarker(list[i]);
    return list.length;
};

AutoCut.clearMarkers = function () {
    try {
        var seq = app.project.activeSequence;
        if (!seq) return AutoCut.err("No active sequence.");
        return AutoCut.ok({ removed: AutoCut._clear(seq) });
    } catch (e) {
        return AutoCut.err(e.toString());
    }
};

/* Jump the playhead of the ORIGINAL sequence to a time (seconds). */
AutoCut.seek = function (p) {
    try {
        var seq = AutoCut.findSequence(p.seqId);
        if (!seq) return AutoCut.err("Original sequence not found.");
        if (!app.project.activeSequence || app.project.activeSequence.sequenceID !== seq.sequenceID) {
            app.project.openSequence(seq.sequenceID);
        }
        var ticks = String(Math.round(p.t * 254016000000));
        seq.setPlayerPosition(ticks);
        return AutoCut.ok();
    } catch (e) {
        return AutoCut.err(e.toString());
    }
};
