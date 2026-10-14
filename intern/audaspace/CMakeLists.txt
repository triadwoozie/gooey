# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

set(INC
  .
)

set(INC_SYS
  ${CMAKE_SOURCE_DIR}/extern/audaspace/include
  ${AUDASPACE_C_INCLUDE_DIRS}
  ${AUDASPACE_PY_INCLUDE_DIRS}
  ${STEAM_AUDIO_INCLUDE_DIR}
)

set(SRC
  plugins/phonon/phonon_device.cc
  plugins/phonon/phonon_device.h
  plugins/phonon/phonon_scene.cc
  plugins/phonon/phonon_scene.h
  plugins/phonon/phonon_dsp.cc
  plugins/phonon/phonon_dsp.h
  plugins/phonon/phonon_simulator.cc
  plugins/phonon/phonon_simulator.h
  plugins/phonon/phonon_reader.cc
  plugins/phonon/phonon_reader.h
  plugins/phonon/phonon_sound.cc
  plugins/phonon/phonon_sound.h
  plugins/phonon/phonon_modal_synth.cc
  plugins/phonon/phonon_modal_synth.h
)

set(LIB
  ${STEAM_AUDIO_LIBRARY}
  bf::dependencies::optional::audaspace
)

add_definitions(-DWITH_STEAM_AUDIO)

blender_add_lib(bf_intern_audaspace "${SRC}" "${INC}" "${INC_SYS}" "${LIB}")
