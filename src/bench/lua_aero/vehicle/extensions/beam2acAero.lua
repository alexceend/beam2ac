-- beam2ac G2.46 (2026-11-14): banco de AERODINÁMICA del conversor (derivado de beam2acDirAero, G2.15):
-- la misma medida sin la fase de dirección (que sustituía hydros.update). Carpeta de usuario AISLADA.
--
-- No modifica el vehículo. Coche parado sobre smallgrid con freno de mano: obj:setWind(-v·dir)
-- (el aire relativo equivale a avanzar a v en la dirección dir) y promedio de
-- obj:calcTotalAeroForces(), obj:calcTotalAeroTorque(p) en los centros de los ejes y
-- obj:calcCenterOfPressureRel() (funciones del juego usadas por
-- lua/vehicle/extensions/aeroDebug.lua), con obj:getAirDensity(). Mismos 16 casos y tiempos
-- que G2.15 (sesión 4); sin fase de dirección.
local M = {}

local logTag = "beam2acAero"
local cfg, out = nil, nil
local phase, step, timer, samples = "idle", 0, 0, nil
local refN = nil
local wheelsList = {}

local AERO = {
  {0, 0, 0}, {20, 0, 0}, {30, 0, 0}, {40, 0, 0}, {50, 0, 0}, {60, 0, 0}, {70, 0, 0},
  {50, 2, 0}, {50, -2, 0}, {50, 4, 0}, {50, -4, 0}, {50, 0, 5}, {50, 0, -5}, {50, 0, 10},
  {50, 0, -10}, {0, 0, 0},
}
local SETTLE_AERO, SAMPLE_AERO = 3.0, 1.0

local function frame()
  local p = function(cid) return obj:getNodePosition(cid) end
  local ref, back, left, up = p(refN.ref), p(refN.back), p(refN.left), p(refN.up)
  local fwd = (ref - back):normalized()
  local lft = (left - ref)
  lft = (lft - fwd * lft:dot(fwd)):normalized()
  local u = fwd:cross(lft):normalized()
  if u:dot(up - ref) < 0 then u = -u end
  return ref, fwd, lft, u
end

local function body(vec, fwd, lft, u)
  return {vec:dot(fwd), vec:dot(lft), vec:dot(u)}
end

local function axleCentres()
  local c = {}
  for _, w in ipairs(wheelsList) do
    local m = (obj:getNodePosition(w.node1) + obj:getNodePosition(w.node2)) * 0.5
    c[w.name] = m
  end
  return c
end

local function sampleAero(case)
  local ref, fwd, lft, u = frame()
  local F = obj:calcTotalAeroForces()
  local c = axleCentres()
  local rec = {v = case[1], pitch = case[2], yaw = case[3], F = body(F, fwd, lft, u),
               rho = obj:getAirDensity(), axles = {}, torque = {}}
  for name, pos in pairs(c) do
    rec.axles[name] = body(pos - ref, fwd, lft, u)
    rec.torque[name] = body(obj:calcTotalAeroTorque(pos), fwd, lft, u)
  end
  local cop = obj:calcCenterOfPressureRel()
  rec.cop = body(cop - ref, fwd, lft, u)
  rec.refHeight = (obj:getPosition() + ref).z
  rec.upZ = u.z
  return rec
end

local function avg(list)
  -- promedio de tablas numéricas anidadas con la misma forma
  local function add(acc, x)
    for k, val in pairs(x) do
      if type(val) == "number" then acc[k] = (acc[k] or 0) + val
      elseif type(val) == "table" then acc[k] = acc[k] or {}; add(acc[k], val) end
    end
  end
  local function div(acc, n)
    for k, val in pairs(acc) do
      if type(val) == "number" then acc[k] = val / n else div(val, n) end
    end
  end
  local acc = {}
  for _, x in ipairs(list) do add(acc, x) end
  div(acc, #list)
  acc.n = #list
  return acc
end

local function setWind(case)
  local _, fwd, lft, u = frame()
  local v, p, y = case[1], math.rad(case[2]), math.rad(case[3])
  local dir = fwd * (math.cos(p) * math.cos(y)) + u * math.sin(p) + lft * (math.cos(p) * math.sin(y))
  local w = dir * (-v)
  obj:setWind(w.x, w.y, w.z)
end

local function finish()
  obj:setWind(0, 0, 0)
  jsonWriteFile("beam2ac_aero/" .. cfg.name .. ".json", out, true)
  log("I", logTag, "terminado " .. cfg.name)
  obj:queueGameEngineLua(string.format("beam2acAeroGE.vehicleDone(%q)", cfg.name))
  phase = "done"
end

function M.start(configJson)
  cfg = jsonDecode(configJson)
  out = {name = cfg.name, config = cfg.config, aero = {}, wheels = {}}
  local r = v.data.refNodes and v.data.refNodes[0]
  refN = {ref = r.ref, back = r.back, left = r.left, up = r.up}
  wheelsList = {}
  for _, w in pairs(wheels.wheels) do
    wheelsList[#wheelsList + 1] = {name = w.name, node1 = w.node1, node2 = w.node2}
    out.wheels[#out.wheels + 1] = w.name
  end
  input.event("parkingbrake", 1, FILTER_DIRECT)
  phase, step, timer, samples = "aero", 1, -1, {}
  setWind(AERO[1])
  log("I", logTag, "inicio " .. cfg.name)
end

function M.updateGFX(dt)
  if phase == "idle" or phase == "done" then return end
  input.event("parkingbrake", 1, FILTER_DIRECT)
  timer = timer + dt
  if phase == "aero" then
    local case = AERO[step]
    setWind(case)
    if timer > SETTLE_AERO then samples[#samples + 1] = sampleAero(case) end
    if timer > SETTLE_AERO + SAMPLE_AERO then
      local a = avg(samples)
      a.v, a.pitch, a.yaw = case[1], case[2], case[3]
      out.aero[#out.aero + 1] = a
      step, timer, samples = step + 1, 0, {}
      if step > #AERO then finish()
      else log("I", logTag, string.format("segmento %d/%d", step, #AERO)) end
    end
  end
end

return M
