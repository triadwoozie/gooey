/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#ifdef WITH_STEAM_AUDIO

#include "IReader.h"
#include "fx/EffectReader.h"
#include "phonon_dsp.h"

#include <memory>
#include <vector>

AUD_NAMESPACE_BEGIN

/**
 * Audaspace effect reader routing audio through Steam Audio's direct simulation
 * (occlusion, transmission EQ, distance attenuation) and binaural HRTF spatialization.
 */
class AUD_API PhononReader : public EffectReader
{
private:
  PhononDSPProcessor *m_dsp;
  std::vector<sample_t> m_temp_in;

public:
  PhononReader(std::shared_ptr<IReader> reader);
  virtual ~PhononReader();

  virtual Specs getSpecs() const override;
  virtual void read(int &length, bool &eos, sample_t *buffer) override;

  void updateParameters(float directivity,
                        float distance_attenuation,
                        float occlusion,
                        const float transmission[3],
                        const float rel_direction[3]);
};

AUD_NAMESPACE_END

#endif /* WITH_STEAM_AUDIO */
