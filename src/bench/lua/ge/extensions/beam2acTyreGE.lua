-- beam2ac G2.20 (2026-11-07): copia de beam2acSuspGE (G2.17) para el banco de neumáticos:
-- recorre beam2ac_tyre/config.json, carga cada vehículo, lanza beam2acTyre y cierra el juego.
-- Se carga con -onLevelLoad_ext beam2acTyreGE en una carpeta de usuario aislada.
local M = {}

local queue, idx, state, timer, current = nil, 0, "wait_level", 0, nil
local logTag = "beam2acTyre"

local function nextVehicle()
  idx = idx + 1
  current = queue[idx]
  timer = 0
  if not current then
    log("I", logTag, "todas las mediciones terminadas")
    state = "quit"
    return
  end
  log("I", logTag, "cargando " .. current.name .. " " .. current.config)
  core_vehicles.replaceVehicle(current.model, {config = current.config})
  state = "spawned"
end

function M.onExtensionLoaded()
  local cfg = jsonReadFile("beam2ac_tyre/config.json")
  queue = cfg and cfg.vehicles or {}
  log("I", logTag, "cola: " .. tostring(#queue))
end

local function keepRunning()
  -- G2.15 (sesión 2): en una carpeta de usuario nueva el juego abre el menú principal y PAUSA la
  -- simulación; se vacía la pila de pausas y se reanuda (igual que G2.15).
  if simTimeAuthority and simTimeAuthority.getPause and simTimeAuthority.getPause() then
    -- G2.20: en esta instalación clearPauseStack no existe siempre; se comprueba antes
    if simTimeAuthority.clearPauseStack then simTimeAuthority.clearPauseStack() end
    simTimeAuthority.pause(false, false)
    log("I", logTag, "pausa levantada")
  end
end

function M.onUpdate(dtReal)
  if not queue then return end
  if state ~= "wait_level" then keepRunning() end
  timer = timer + dtReal
  if state == "wait_level" then
    if timer > 8 and be:getPlayerVehicle(0) then
      nextVehicle()
    end
  elseif state == "spawned" then
    if timer > 12 then
      local v = be:getPlayerVehicle(0)
      if v then
        local cmd = string.format("extensions.load('beam2acTyre'); beam2acTyre.start(%q)", jsonEncode(current))
        v:queueLuaCommand(cmd)
        state = "running"
        timer = 0
      end
    end
  elseif state == "running" then
    if timer > (current.timeout or 3600) then
      log("E", logTag, "tiempo agotado: " .. current.name)
      nextVehicle()
    end
  elseif state == "quit" then
    if timer > 3 then
      state = "closed"
      shutdown(0)
    end
  end
end

function M.vehicleDone(name)
  log("I", logTag, "terminado " .. tostring(name))
  if state == "running" then
    nextVehicle()
  end
end

return M
