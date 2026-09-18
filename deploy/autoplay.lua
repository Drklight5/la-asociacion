-- deploy/autoplay.lua -- La Asociación
--
-- El lanzador (deploy/INICIAR-Windows.bat / INICIAR-macOS.command) corre este
-- ReaScript en Reaper unos segundos despues de abrir reaperProject.RPP
-- (config: AUTOPLAY / AUTOPLAY_DELAY). Va al inicio del proyecto y le da play,
-- para arrancar la obra sin tocar Reaper. No hace nada si ya esta sonando.

if (reaper.GetPlayState() & 1) == 0 then
  reaper.Main_OnCommand(40042, 0)  -- Transport: Go to start of project
  reaper.Main_OnCommand(1013, 0)   -- Transport: Record
end
