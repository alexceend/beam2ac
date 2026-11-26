// Construye cars/<CAR> en una COPIA del proyecto del SDK (fmod_project/). Todos los objetos
// se crean con studio.project.create(): FMOD genera sus GUID. Datos en spec.json (make_spec.py).
var SPEC = /*SPEC*/null;
var p = studio.project;
var LOG = [];
function log(m) { LOG.push(m); flushLog(); }
function flushLog() {
    var f = studio.system.getFile(p.filePath.replace(/[^\/\\]*$/, "") + "../build_events_log.txt");
    if (f.open(studio.system.openMode.WriteOnly)) { f.writeText(LOG.join("\n") + "\n"); f.close(); }
}
function setProps(obj, props) {
    for (var k in props) { if (props[k] !== null && props[k] !== undefined) { obj[k] = props[k]; } }
}
function point(curveOrNull, pos, val, shape) {
    var pt = p.create("AutomationPoint");
    pt.position = pos; pt.value = val;
    if (shape !== null && shape !== undefined) { pt.curveShape = shape; }
    if (curveOrNull) { curveOrNull.relationships.automationPoints.add(pt); }
    return pt;
}
function curveOn(param, autoTrack, pts) {
    var c = p.create("AutomationCurve");
    autoTrack.relationships.automationCurves.add(c);
    param.relationships.automationCurves.add(c);
    for (var i = 0; i < pts.length; i++) { point(c, pts[i][0], pts[i][1], pts[i][2]); }
    return c;
}
function addEffect(bus, fx, index) {
    var chain = bus.effectChain;
    if (index === null) { chain.relationships.effects.add(fx); } else { chain.relationships.effects.insert(index, fx); }
}

try {
    var cars = p.lookup(SPEC.cars_folder);
    if (!cars) { throw "no existe la carpeta cars"; }
    var items = cars.items || [];
    for (var i = 0; i < items.length; i++) { if (items[i].name === SPEC.car) { throw "ya existe cars/" + SPEC.car; } }
    var folder = p.create("EventFolder"); folder.name = SPEC.car; folder.folder = cars;
    var bank = p.create("Bank"); bank.name = SPEC.car; bank.folder = p.lookup(SPEC.bank_folder);
    log("carpeta " + folder.id + " banco " + bank.id);

    // audio: una importación por WAV (FMOD copia el archivo a Assets/)
    var audio = {};
    for (var a = 0; a < SPEC.audio_files.length; a++) {
        var af = p.importAudioFile(SPEC.audio_files[a]);
        if (!af) { throw "importAudioFile falló: " + SPEC.audio_files[a]; }
        audio[SPEC.audio_files[a]] = af;
    }
    log("audio importado: " + SPEC.audio_files.length);

    function baseEvent(name, d) {
        var ev = p.create("Event");
        ev.name = name; ev.folder = folder;
        if (d.outputFormat !== null) { ev.outputFormat = parseInt(d.outputFormat, 10); }
        ev.relationships.banks.add(bank);
        var grp = p.lookup(d.group);
        if (!grp) { throw name + ": grupo no encontrado " + d.group; }
        ev.mixerInput.output = grp;
        setProps(ev.automatableProperties, d.automatable);
        if (d.spatialiser) {
            var sp = p.create("SpatialiserEffect"); setProps(sp, d.spatialiser);
            addEffect(ev.mixer.masterBus, sp, 0);
        }
        return ev;
    }
    function gameParam(ev, props) {
        var gp = p.create("GameParameter"); setProps(gp, props);
        ev.relationships.parameters.add(gp);
        return gp;
    }

    // eventos secundarios: silenciosos (sin pistas ni audio), mismos parámetros que la plantilla
    for (var name in SPEC.secondary) {
        var d = SPEC.secondary[name];
        var autos = {};
        for (var k in d.automatable) { autos[k] = (d.automatable[k] === "true") ? true : parseFloat(d.automatable[k]); }
        var sd = { outputFormat: d.outputFormat, group: d.group, automatable: autos, spatialiser: null };
        if (d.spatialiser) {
            sd.spatialiser = {};
            for (var s in d.spatialiser) { sd.spatialiser[s] = (d.spatialiser[s] === "true") ? true : parseFloat(d.spatialiser[s]); }
        }
        var ev = baseEvent(name, sd);
        for (var j = 0; j < d.parameters.length; j++) {
            var gp = {};
            for (var q in d.parameters[j]) { gp[q] = (q === "name") ? d.parameters[j][q] : parseFloat(d.parameters[j][q]); }
            gameParam(ev, gp);
        }
        log("silencioso " + name + " " + ev.id);
    }

    // motor
    for (var en in SPEC.engine) {
        var e = SPEC.engine[en];
        var ev = baseEvent(en, e);
        var rpms = gameParam(ev, { name: "rpms", maximum: e.rpms_max });
        var thr = gameParam(ev, { name: "throttle" });
        if (e.lowpass) {
            var ang = p.create("AngleParameter"); ang.maximum = 180; ev.relationships.parameters.add(ang);
            var lp = p.create("LowpassEffect"); lp.cutoff = e.lowpass.cutoff;
            addEffect(ev.mixer.masterBus, lp, null);
            var lat = p.create("AutomationTrack"); lat.nameOfPropertyBeingAutomated = "cutoff"; lat.objectBeingAutomated = lp;
            ev.masterTrack.relationships.automationTracks.add(lat);
            curveOn(ang, lat, e.lowpass.angle_curve);
        }
        for (var t = 0; t < e.tracks.length; t++) {
            var td = e.tracks[t];
            var tr = p.create("GroupTrack");
            var g = tr.mixerGroup;
            if (!g) { g = p.create("EventMixerGroup"); tr.mixerGroup = g; }
            g.name = td.name; g.output = ev.mixer.masterBus;
            ev.relationships.groupTracks.add(tr);
            if (td.eq) { var eq = p.create("ThreeEQEffect"); setProps(eq, td.eq); addEffect(g, eq, 0); }
            var at = p.create("AutomationTrack"); at.nameOfPropertyBeingAutomated = "volume"; at.objectBeingAutomated = g;
            tr.relationships.automationTracks.add(at);
            curveOn(thr, at, td.throttle_curve);
            var prev = null;
            var made = [];
            for (var n = 0; n < td.sounds.length; n++) {
                var sd2 = td.sounds[n];
                var ss = p.create("SingleSound");
                ss.start = sd2.start; ss.length = sd2.length; ss.looping = true; ss.voiceStealing = 3;
                ss.audioFile = audio[sd2.file];
                var ap = p.create("AutopitchModulator"); ap.nameOfPropertyBeingModulated = "pitch"; ap.root = sd2.rpm;
                ss.relationships.modulators.add(ap);
                tr.relationships.modules.add(ss);
                rpms.relationships.modules.add(ss);
                made.push(ss);
            }
            for (var m = 0; m < made.length; m++) {
                var info = td.sounds[m];
                if (info.fade_in) {
                    var fi = p.create("FadeCurve");
                    fi.startPoint = point(null, info.fade_in[0], 0, -SPEC.fade_shape);
                    fi.endPoint = point(null, info.fade_in[1], 1, null);
                    fi.relatedModule = made[m - 1];
                    made[m].fadeInCurve = fi;
                }
                if (info.fade_out) {
                    var fo = p.create("FadeCurve");
                    fo.startPoint = point(null, info.fade_out[0], 1, SPEC.fade_shape);
                    fo.endPoint = point(null, info.fade_out[1], 0, null);
                    fo.relatedModule = made[m + 1];
                    made[m].fadeOutCurve = fo;
                }
            }
            log(en + " pista " + td.name + ": " + made.length + " sonidos");
        }
        log("motor " + en + " " + ev.id);
    }
    p.save();
    log("GUARDADO");
} catch (err) {
    log("ERROR: " + err);
}
flushLog();
