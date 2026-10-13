# SPDX-FileCopyrightText: 2003-2026 Paul Brossier & Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

set(INC
  src
  .
)

set(INC_SYS
)

set(SRC
  src/cvec.c
  src/fmat.c
  src/fvec.c
  src/lvec.c
  src/mathutils.c
  src/musicutils.c
  src/vecutils.c
  src/utils/hist.c
  src/utils/log.c
  src/utils/parameter.c
  src/utils/scale.c
  src/utils/strutils.c
  src/spectral/awhitening.c
  src/spectral/dct.c
  src/spectral/dct_ooura.c
  src/spectral/dct_plain.c
  src/spectral/fft.c
  src/spectral/filterbank.c
  src/spectral/filterbank_mel.c
  src/spectral/mfcc.c
  src/spectral/ooura_fft8g.c
  src/spectral/phasevoc.c
  src/spectral/specdesc.c
  src/spectral/statistics.c
  src/spectral/tss.c
  src/temporal/a_weighting.c
  src/temporal/biquad.c
  src/temporal/c_weighting.c
  src/temporal/filter.c
  src/temporal/resampler.c
  src/pitch/pitch.c
  src/pitch/pitchfcomb.c
  src/pitch/pitchmcomb.c
  src/pitch/pitchschmitt.c
  src/pitch/pitchspecacf.c
  src/pitch/pitchyin.c
  src/pitch/pitchyinfast.c
  src/pitch/pitchyinfft.c
  src/onset/onset.c
  src/onset/peakpicker.c
  src/tempo/beattracking.c
  src/tempo/tempo.c
  src/notes/notes.c
  src/synth/sampler.c
  src/synth/wavetable.c
)

add_definitions(-DHAVE_CONFIG_H)

blender_add_lib(extern_aubio "${SRC}" "${INC}" "${INC_SYS}" "")
