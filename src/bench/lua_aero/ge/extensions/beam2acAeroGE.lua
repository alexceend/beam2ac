-- beam2ac G2.46 (2026-11-14): copia de beam2acDirAeroGE (G2.15) para medir SOLO aerodinámica:
-- recorre beam2ac_aero/config.json, carga cada vehículo, lanza beam2acAero y cierra el juego.
-- Se carga con -onLevelLoad_ext beam2acAeroGE en una carpeta de usuario aislada.
local M = {}

local queue, idx, state, timer, current = nil, 0, "wait_level", 0, nil
local logTag = "beam2acAero"

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
  local cfg = jsonReadFile("beam2ac_aero/config.json")
  queue = cfg and cfg.vehicles or {}
  log("I", logTag, "cola: " .. tostring(#queue))
end

local function keepRunning()
  -- G2.15 (sesión 2): en una carpeta de usuario nueva el juego abre el menú principal
  -- («discover») tras cargar y PAUSA la simulación (simTimeAuthority.pushPauseRequest);
  -- en la sesión 1 el banco quedó detenido. Se vacía la pila de pausas y se reanuda.
  if simTimeAuthority and simTimeAuthority.getPause and simTimeAuthority.getPause() then
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
        local cmd = string.format("extensions.load('beam2acAero'); beam2acAero.start(%q)", jsonEncode(current))
        v:queueLuaCommand(cmd)
        state = "running"
        timer = 0
      end
    end
  elseif state == "running" then
    if timer > (current.timeout or 900) then
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
