-- beam2ac G2.20 (2026-11-07): banco de neumáticos (carpeta de usuario AISLADA; vehículo sin modificar).
--
-- «Banco virtual»: el coche completo rueda sobre smallgrid y se le imponen el estado de la
-- carrocería y la velocidad de giro de cada rueda; se mide la fuerza de contacto de cada rueda.
--
-- * Carrocería: fuerzas EXTERNAS proporcionales a la masa en todos los nodos que no son de rueda
--   (F_i = m_i·(a + α ẑ×(r_i − r_cg)) − m_i·g·kload·ẑ; obj:applyForceVector, como
--   playerController.lua). Un PI sobre la velocidad horizontal mantiene el módulo V y la
--   dirección ψ0 + β (β = ángulo de deriva impuesto a la carrocería) y un PID de guiñada mantiene
--   el rumbo ψ0. kload > 0 añade carga vertical (fracción del peso), < 0 la quita.
--   La fuerza horizontal del control se aplica «a la altura del suelo»: se añade un par de
--   fuerzas verticales F_z,i = m_i·(y_i·M·h·a_izq/Σm·y² + x_i·M·h·a_adel/Σm·x²) (x, y en el marco
--   de la carrocería respecto al CdG, h = altura del CdG sobre el suelo), que anula el momento de
--   balanceo y cabeceo de la fuerza aplicada en el CdG. Así no hay transferencia de carga: cada
--   rueda trabaja a su carga estática (× (1 + kload)), como en un banco de neumáticos.
-- * Ruedas: el par de cada rueda se SUSTITUYE en cada paso de física después de
--   wheels.updateWheelTorques (main.lua: el gancho de extensiones va después), escribiendo en
--   wd.coreData (propulsionTorque, brakingTorque = 0, engineReactionTorque = 0). Ruedas
--   «libres»: par 0. Ruedas con deslizamiento: PI sobre la velocidad angular con objetivo
--   ω = (1 + κ)·v_x/r_libre (r_libre medido antes en rodadura libre).
-- * Fuerza de contacto: el conjunto rotatorio W de cada rueda (nodos de banda + buje + nodos
--   colgados solo de ellos) recibe del resto del coche la suma de las vigas que cruzan la
--   frontera; se acumula S = Σ getBeamStress·unidad(getBeamVectorFromNode(viga, nodo_de_W)),
--   el mismo método que lua/vehicle/extensions/tech/wheelForces.lua del juego. El signo se
--   fija en el análisis con el equilibrio estático (Σ Fz = peso). Fuerza de contacto media =
--   −(fuerza de las vigas sobre W) − m_W·g (en régimen, la aceleración media de W es nula).
--
-- Excitación vertical (segmentos con ``vosc`` = {a, f}): fuerza interna F = a·(M·g/4)·sin(2π f t)
-- hacia arriba repartida entre node1 y node2 de cada rueda y la opuesta repartida por masa en la
-- carrocería (sin fuerza neta): da la rigidez y la amortiguación verticales del neumático
-- (Fz frente a la altura del buje y su derivada). Esos segmentos se registran con ``win`` pasos.
--
-- Registro (50 Hz, medias de 40 pasos de 0,5 ms; ``win`` del segmento si lo tiene): por rueda S (x,y,z mundo), centro del buje y su
-- velocidad, eje (node1→node2), ω·wheelDir, par impuesto, κ objetivo, z mínima de la banda,
-- temperatura media, presión del grupo y downForceRaw del juego.
local M = {}

local logTag = "beam2acTyre"
local WIN = 40 -- pasos por fila (50 Hz)
local G = 9.81

local cfg, meta = nil, nil
local phase = "idle"
local segs, segIdx, segT = {}, 0, 0
local stepCount, winCount = 0, 0
local body, bodyMass, Iz = {}, 0, 0
local wl = {}
local refN = nil
local rows = {}
local psi0, fwd0 = 0, nil
local vInt = vec3(0, 0, 0)
local yawInt, lastPsi = 0, nil
local yawRate = 0
local cg = vec3(0, 0, 0)
local acc = vec3(0, 0, 0)
local alphaCmd = 0
local vPrev = 0
local wallStart = 0
local nodeCount = 0
local fwdB, leftB = vec3(1, 0, 0), vec3(0, 1, 0)
local sxx, syy, hcg = 1, 1, 0

-- ------------------------------------------------------------------ utilidades
local function wrap(a)
  while a > math.pi do a = a - 2 * math.pi end
  while a < -math.pi do a = a + 2 * math.pi end
  return a
end

local function profile(spec, t, dur)
  if type(spec) == "number" then return spec end
  if type(spec) ~= "table" then return 0 end
  local x = (dur > 0) and math.min(math.max(t / dur, 0), 1) or 1
  if spec.kind == "tri" then -- 0 → a → −a → 0
    local a = spec.a
    if x < 0.25 then return 4 * a * x end
    if x < 0.75 then return a - 4 * a * (x - 0.25) end
    return -a + 4 * a * (x - 0.75)
  elseif spec.kind == "ramp" then
    return spec.from + (spec.to - spec.from) * x
  elseif spec.kind == "steps" then
    local n = #spec.values
    local i = math.min(n, math.floor(x * n) + 1)
    return spec.values[i]
  end
  return 0
end

local function heading()
  local ref, back = obj:getNodePosition(refN.ref), obj:getNodePosition(refN.back)
  local f = ref - back
  f.z = 0
  return f:normalized()
end

-- ------------------------------------------------------------------ medida
local function wheelForce(w)
  local sx, sy, sz = 0, 0, 0
  for k = 1, #w.cross do
    local c = w.cross[k]
    local s = obj:getBeamStress(c.cid)
    local d = obj:getBeamVectorFromNode(c.cid, c.wnode)
    local len = d:length()
    if len > 1e-9 then
      local f = s / len
      sx = sx + d.x * f
      sy = sy + d.y * f
      sz = sz + d.z * f
    end
  end
  return sx, sy, sz
end

local function hubState(w)
  local p1, p2 = obj:getNodePosition(w.node1), obj:getNodePosition(w.node2)
  local v1, v2 = obj:getNodeVelocityVector(w.node1), obj:getNodeVelocityVector(w.node2)
  return (p1 + p2) * 0.5, (v1 + v2) * 0.5, (p2 - p1)
end

local function minTreadZ(w)
  local z = math.huge
  for k = 1, #w.tread do
    local p = obj:getNodePosition(w.tread[k])
    if p.z < z then z = p.z end
  end
  return z
end

-- ------------------------------------------------------------------ control de las ruedas
local function wheelLongVel(w, hv, axis)
  -- dirección de rodadura: horizontal y perpendicular al eje, con el sentido de la carrocería
  local h = vec3(-axis.y, axis.x, 0)
  if h:dot(fwd0) < 0 then h = -h end
  h = h:normalized()
  return hv:dot(h)
end

local function setWheelTorques(seg, dt)
  for i = 1, #wl do
    local w = wl[i]
    local t = w.wd.coreData
    local T = 0
    if w.slipOn then
      local _, hv, axis = hubState(w)
      local vx = wheelLongVel(w, hv, axis)
      local kap = profile(seg.kappa, segT, seg.dur)
      w.kapCmd = kap
      local omT = (1 + kap) * vx / w.rfree
      local om = t.angularVelocity * w.dir
      local e = omT - om
      w.eInt = w.eInt + e * dt
      local lim = 9000
      local iMax = lim / w.Ki
      if w.eInt > iMax then w.eInt = iMax elseif w.eInt < -iMax then w.eInt = -iMax end
      T = w.Kp * e + w.Ki * w.eInt
      if T > lim then T = lim elseif T < -lim then T = -lim end
    else
      w.kapCmd = 0
      w.eInt = 0
    end
    w.T = T
    t.propulsionTorque = T * w.dir
    t.brakingTorque = 0
    t.engineReactionTorque = 0
  end
end

-- ------------------------------------------------------------------ control de la carrocería
local function updateBodyGeometry()
  local m, c = 0, vec3(0, 0, 0)
  for k = 1, #body do
    local b = body[k]
    local p = obj:getNodePosition(b.cid)
    b.px, b.py = p.x, p.y
    c = c + p * b.m
    m = m + b.m
  end
  cg = c / m
  fwdB = heading()
  leftB = vec3(-fwdB.y, fwdB.x, 0)
  local iz, a, bsum = 0, 0, 0
  for k = 1, #body do
    local b = body[k]
    b.rx, b.ry = b.px - cg.x, b.py - cg.y
    b.bx = b.rx * fwdB.x + b.ry * fwdB.y
    b.by = b.rx * leftB.x + b.ry * leftB.y
    iz = iz + b.m * (b.rx * b.rx + b.ry * b.ry)
    a = a + b.m * b.bx * b.bx
    bsum = bsum + b.m * b.by * b.by
  end
  Iz, sxx, syy = iz, a, bsum
  -- suelo = media de la z mínima de la banda de las 4 ruedas
  if #wl > 0 and wl[1].tread then
    local zg = 0
    for i = 1, #wl do zg = zg + minTreadZ(wl[i]) end
    hcg = cg.z - zg / #wl
  end
end

local function bodyControl(seg, dt)
  local V = profile(seg.V, segT, seg.dur)
  local beta = math.rad(profile(seg.beta or 0, segT, seg.dur))
  local kload = profile(seg.kload or 0, segT, seg.dur)
  seg._V, seg._beta, seg._kload = V, beta, kload
  -- velocidad medida: media de los 4 nodos de referencia
  local v = (obj:getNodeVelocityVector(refN.ref) + obj:getNodeVelocityVector(refN.back)
    + obj:getNodeVelocityVector(refN.left) + obj:getNodeVelocityVector(refN.up)) * 0.25
  v.z = 0
  local ang = psi0 + beta
  local vd = vec3(math.cos(ang) * V, math.sin(ang) * V, 0)
  local e = vd - v
  local Kp, Ki = 6.0, 20.0
  vInt = vInt + e * dt
  local lim = 2.5 * G / Ki
  local il = vInt:length()
  if il > lim then vInt = vInt * (lim / il) end
  acc = e * Kp + vInt * Ki
  -- guiñada
  local f = heading()
  local psi = math.atan2(f.y, f.x)
  if lastPsi then
    local r = wrap(psi - lastPsi) / dt
    yawRate = yawRate + (r - yawRate) * math.min(1, dt * 200)
  end
  lastPsi = psi
  local ep = wrap(psi - psi0)
  yawInt = yawInt + ep * dt
  local yl = 3.0 / 80
  if yawInt > yl then yawInt = yl elseif yawInt < -yl then yawInt = -yl end
  alphaCmd = -40 * ep - 14 * yawRate - 80 * yawInt
  local az = -G * kload
  local cx, cy = 0, 0
  if cfg.groundForce ~= false then
    local aF = acc.x * fwdB.x + acc.y * fwdB.y
    local aL = acc.x * leftB.x + acc.y * leftB.y
    cx = bodyMass * hcg * aF / sxx
    cy = bodyMass * hcg * aL / syy
  end
  for k = 1, #body do
    local b = body[k]
    local fx = b.m * (acc.x - alphaCmd * b.ry)
    local fy = b.m * (acc.y + alphaCmd * b.rx)
    obj:applyForceVector(b.cid, vec3(fx, fy, b.m * (az + cx * b.bx + cy * b.by)))
  end
end

-- ------------------------------------------------------------------ registro
local function header()
  local h = {"seg", "t", "Vcmd", "betacmd", "kload", "vx", "vy", "vz", "psi", "ax", "ay", "alpha", "yawrate"}
  for i = 1, #wl do
    h[#h + 1] = string.format(
      "S%dx,S%dy,S%dz,p%dx,p%dy,p%dz,v%dx,v%dy,v%dz,a%dx,a%dy,a%dz,om%d,T%d,k%d,zt%d,temp%d,pr%d,df%d",
      i, i, i, i, i, i, i, i, i, i, i, i, i, i, i, i, i, i, i)
  end
  return table.concat(h, ",")
end

local function resetAcc()
  for i = 1, #wl do
    local w = wl[i]
    w.aS = {0, 0, 0}
    w.aP = {0, 0, 0}
    w.aV = {0, 0, 0}
    w.aA = {0, 0, 0}
    w.aOm, w.aT = 0, 0
  end
  winCount = 0
end

local function accumulate()
  for i = 1, #wl do
    local w = wl[i]
    local sx, sy, sz = wheelForce(w)
    local p, hv, axis = hubState(w)
    w.aS[1], w.aS[2], w.aS[3] = w.aS[1] + sx, w.aS[2] + sy, w.aS[3] + sz
    w.aP[1], w.aP[2], w.aP[3] = w.aP[1] + p.x, w.aP[2] + p.y, w.aP[3] + p.z
    w.aV[1], w.aV[2], w.aV[3] = w.aV[1] + hv.x, w.aV[2] + hv.y, w.aV[3] + hv.z
    w.aA[1], w.aA[2], w.aA[3] = w.aA[1] + axis.x, w.aA[2] + axis.y, w.aA[3] + axis.z
    w.aOm = w.aOm + w.wd.coreData.angularVelocity * w.dir
    w.aT = w.aT + (w.T or 0)
  end
  winCount = winCount + 1
end

local function record(seg)
  local n = winCount
  if n == 0 then return end
  local v = obj:getNodeVelocityVector(refN.ref)
  local f = heading()
  local r = {segIdx, string.format("%.4f", segT), string.format("%.5g", seg._V or 0),
    string.format("%.6g", seg._beta or 0), string.format("%.4g", seg._kload or 0),
    string.format("%.6g", v.x), string.format("%.6g", v.y), string.format("%.6g", v.z),
    string.format("%.7g", math.atan2(f.y, f.x)), string.format("%.5g", acc.x), string.format("%.5g", acc.y),
    string.format("%.5g", alphaCmd), string.format("%.5g", yawRate)}
  for i = 1, #wl do
    local w = wl[i]
    local pr = 0
    if w.pgroup then pr = obj:getGroupPressure(w.pgroup) end
    local df = 0
    local ok, _, _, _, d = pcall(function() return w.wd.obj:getSlipVelEnergyDownPeakForceDepthMats() end)
    if ok and type(d) == "number" then df = d end
    local temp = 0
    local okT, tv = pcall(function() return obj:getWheelAvgTemperature(w.wd.wheelID) end)
    if okT and type(tv) == "number" then temp = tv end
    r[#r + 1] = string.format(
      "%.7g,%.7g,%.7g,%.8g,%.8g,%.8g,%.7g,%.7g,%.7g,%.7g,%.7g,%.7g,%.7g,%.6g,%.5g,%.7g,%.5g,%.7g,%.6g",
      w.aS[1] / n, w.aS[2] / n, w.aS[3] / n, w.aP[1] / n, w.aP[2] / n, w.aP[3] / n,
      w.aV[1] / n, w.aV[2] / n, w.aV[3] / n, w.aA[1] / n, w.aA[2] / n, w.aA[3] / n,
      w.aOm / n, w.aT / n, w.kapCmd or 0, minTreadZ(w), temp, pr, df)
  end
  rows[#rows + 1] = table.concat(r, ",")
  resetAcc()
end

-- ------------------------------------------------------------------ segmentos
local function measureRfree(seg)
  -- r_libre = media de v_x/ω en la segunda mitad del segmento (par 0)
  for i = 1, #wl do
    local w = wl[i]
    if w.rN > 0 then
      w.rfree = w.rSum / w.rN
      meta.wheels[i]["rfree_" .. seg.name] = w.rfree
    end
    w.rSum, w.rN = 0, 0
  end
end

local function startSeg(i)
  segIdx, segT = i, 0
  local seg = segs[i]
  if not seg then return end
  for k = 1, #wl do
    local w = wl[k]
    w.slipOn = seg.kappa ~= nil and (seg.axles == nil or seg.axles == "all" or seg.axles == w.axle)
    w.eInt = 0
    w.rSum, w.rN = 0, 0
    if w.pgroup and seg.pfac then
      obj:setGroupPressure(w.pgroup, 101325 + (w.p0 - 101325) * seg.pfac)
    end
  end
  meta.segStartWall[i] = os.clock() - wallStart
  log("I", logTag, string.format("segmento %d %s (%.1f s)", i, seg.name, seg.dur))
end

local function onPhysicsStep(dt)
  if phase ~= "run" then return end
  local seg = segs[segIdx]
  if not seg then return end
  segT = segT + dt
  stepCount = stepCount + 1
  if stepCount % 200 == 0 then updateBodyGeometry() end
  bodyControl(seg, dt)
  setWheelTorques(seg, dt)
  if seg.vosc then
    local F = seg.vosc.a * meta.totalMass * G / 4 * math.sin(2 * math.pi * seg.vosc.f * segT)
    for i = 1, #wl do
      obj:applyForceVector(wl[i].node1, vec3(0, 0, F * 0.5))
      obj:applyForceVector(wl[i].node2, vec3(0, 0, F * 0.5))
    end
    local azb = -4 * F / bodyMass
    for k = 1, #body do
      local b = body[k]
      obj:applyForceVector(b.cid, vec3(0, 0, b.m * azb))
    end
  end
  if seg.rfree and segT > seg.dur * 0.5 then
    for i = 1, #wl do
      local w = wl[i]
      local _, hv, axis = hubState(w)
      local om = w.wd.coreData.angularVelocity * w.dir
      if om > 1 then
        w.rSum = w.rSum + wheelLongVel(w, hv, axis) / om
        w.rN = w.rN + 1
      end
    end
  end
  if seg.record ~= false then
    accumulate()
    if winCount >= (seg.win or WIN) then record(seg) end
  end
  if segT >= seg.dur then
    if seg.rfree then measureRfree(seg) end
    resetAcc()
    if segIdx >= #segs then
      phase = "write"
    else
      startSeg(segIdx + 1)
    end
  end
end

local function finish()
  for k = 1, #wl do
    local w = wl[k]
    if w.pgroup then obj:setGroupPressure(w.pgroup, w.p0) end
  end
  meta.wallSeconds = os.clock() - wallStart
  meta.steps = stepCount
  local name = cfg.name .. (cfg.tag and ("_" .. cfg.tag) or "")
  writeFile("beam2ac_tyre/" .. name .. ".csv", header() .. "\n" .. table.concat(rows, "\n") .. "\n")
  jsonWriteFile("beam2ac_tyre/" .. name .. ".meta.json", meta, true)
  log("I", logTag, "terminado " .. name .. " filas=" .. tostring(#rows))
  obj:queueGameEngineLua(string.format("beam2acTyreGE.vehicleDone(%q)", cfg.name))
  phase = "done"
end

-- ------------------------------------------------------------------ inicio
local function nodeName(cid)
  local n = v.data.nodes[cid]
  return n and n.name
end

function M.start(configJson)
  cfg = jsonDecode(configJson)
  wallStart = os.clock()
  local r = v.data.refNodes and v.data.refNodes[0]
  refN = {ref = r.ref, back = r.back, left = r.left, up = r.up}
  fwd0 = heading()
  psi0 = math.atan2(fwd0.y, fwd0.x)

  -- conjuntos rotatorios
  local inWheel = {}
  wl = {}
  for id, wd in pairs(wheels.wheels) do
    local vw = v.data.wheels[id]
    local w = {wd = wd, name = wd.name, node1 = wd.node1, node2 = wd.node2, dir = wd.wheelDir, set = {},
      tread = {}, cross = {}}
    for _, n in pairs(vw.treadNodes or {}) do w.set[n] = true; w.tread[#w.tread + 1] = n end
    for _, n in pairs(vw.nodes or {}) do w.set[n] = true end
    wl[#wl + 1] = w
  end
  table.sort(wl, function(a, b) return a.name < b.name end)
  -- nodos colgados solo del conjunto (tapacubos): se añaden hasta que no cambia
  local adj = {}
  for _, b in pairs(v.data.beams) do
    if type(b) == "table" and b.id1 and b.id2 then
      adj[b.id1] = adj[b.id1] or {}
      adj[b.id2] = adj[b.id2] or {}
      table.insert(adj[b.id1], b.id2)
      table.insert(adj[b.id2], b.id1)
    end
  end
  for _, w in ipairs(wl) do
    local changed = true
    while changed do
      changed = false
      for cid, nb in pairs(adj) do
        if not w.set[cid] and cid ~= w.node1 and cid ~= w.node2 then
          local all, any = true, false
          for _, o in ipairs(nb) do
            if w.set[o] then any = true else all = false end
          end
          if any and all then w.set[cid] = true; changed = true end
        end
      end
    end
    for cid, _ in pairs(w.set) do inWheel[cid] = true end
  end
  for _, b in pairs(v.data.beams) do
    if type(b) == "table" and b.id1 and b.id2 then
      for _, w in ipairs(wl) do
        local a, c = w.set[b.id1], w.set[b.id2]
        if a and not c then w.cross[#w.cross + 1] = {cid = b.cid, wnode = b.id1}
        elseif c and not a then w.cross[#w.cross + 1] = {cid = b.cid, wnode = b.id2} end
      end
    end
  end

  -- masa e inercia de cada conjunto (respecto a la recta node1–node2)
  local total = 0
  body = {}
  nodeCount = 0
  for cid, _ in pairs(v.data.nodes) do
    if type(cid) == "number" then
      local m = obj:getNodeMass(cid)
      total = total + m
      nodeCount = nodeCount + 1
      if not inWheel[cid] then body[#body + 1] = {cid = cid, m = m} end
    end
  end
  bodyMass = 0
  for _, b in ipairs(body) do bodyMass = bodyMass + b.m end
  for _, w in ipairs(wl) do
    local p1, p2 = obj:getNodePosition(w.node1), obj:getNodePosition(w.node2)
    local ax = (p2 - p1):normalized()
    local m, I, n = 0, 0, 0
    for cid, _ in pairs(w.set) do
      local mi = obj:getNodeMass(cid)
      local d = obj:getNodePosition(cid) - p1
      local rr = d - ax * d:dot(ax)
      m = m + mi
      I = I + mi * rr:dot(rr)
      n = n + 1
    end
    w.mass, w.inertia, w.nset = m, I, n
    w.Kp, w.Ki = 80 * I, 1600 * I
    w.rfree = v.data.wheels[w.wd.wheelID] and v.data.wheels[w.wd.wheelID].radius or 0.3
    local c = (p1 + p2) * 0.5
    w.axle = (c - obj:getNodePosition(refN.ref)):dot(fwd0) > 0 and "front" or "rear"
    w.pgroup = w.wd.pressureGroup and v.data.pressureGroups and v.data.pressureGroups[w.wd.pressureGroup]
    w.p0 = w.pgroup and obj:getGroupPressure(w.pgroup) or 0
  end
  updateBodyGeometry()

  segs = cfg.segments or {}
  meta = {name = cfg.name, config = cfg.config, tag = cfg.tag, totalMass = total, bodyMass = bodyMass,
    nodes = nodeCount, Iz0 = Iz, hcg0 = hcg, groundForce = cfg.groundForce ~= false, WIN = WIN, physicsDt = 0.0005, psi0 = psi0, segments = segs,
    segStartWall = {}, wheels = {}}
  for i, w in ipairs(wl) do
    local vw = v.data.wheels[w.wd.wheelID] or {}
    meta.wheels[i] = {name = w.name, node1 = nodeName(w.node1), node2 = nodeName(w.node2), wheelDir = w.dir,
      axle = w.axle, mass = w.mass, inertia = w.inertia, nodesInSet = w.nset, crossingBeams = #w.cross,
      treadNodes = #w.tread, radius = vw.radius, hubRadius = vw.hubRadius, tireWidth = vw.tireWidth,
      numRays = vw.numRays, pressurePSI = vw.pressurePSI, pressureGroup = w.wd.pressureGroup,
      pressure0Pa = w.p0, frictionCoef = vw.frictionCoef, slidingFrictionCoef = vw.slidingFrictionCoef,
      noLoadCoef = vw.noLoadCoef, fullLoadCoef = vw.fullLoadCoef,
      loadSensitivitySlope = vw.loadSensitivitySlope, softnessCoef = vw.softnessCoef,
      treadCoef = vw.treadCoef, stribeckExponent = vw.stribeckExponent, stribeckVelMult = vw.stribeckVelMult}
  end

  rows, stepCount = {}, 0
  resetAcc()
  -- motor apagado y frenos sueltos: el par de las ruedas lo impone el banco
  pcall(function() electrics.setIgnitionLevel(0) end)
  input.event("parkingbrake", 0, FILTER_DIRECT)
  input.event("brake", 0, FILTER_DIRECT)
  input.event("throttle", 0, FILTER_DIRECT)
  input.event("steering", 0, FILTER_DIRECT)
  enablePhysicsStepHook()
  startSeg(1)
  phase = "run"
  log("I", logTag, string.format("inicio %s masa=%.1f carroceria=%.1f nodos=%d ruedas=%d Iz=%.0f",
    cfg.name, total, bodyMass, nodeCount, #wl, Iz))
end

function M.updateGFX(dt)
  if phase == "run" then
    input.event("parkingbrake", 0, FILTER_DIRECT)
    input.event("brake", 0, FILTER_DIRECT)
    input.event("throttle", 0, FILTER_DIRECT)
    input.event("steering", 0, FILTER_DIRECT)
  elseif phase == "write" then
    finish()
  end
end

M.onPhysicsStep = onPhysicsStep

return M
