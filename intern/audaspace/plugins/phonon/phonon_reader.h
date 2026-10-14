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
 *
 * When m_speaker_key is non-null this reader self-registers into the global
 * phonon_sound registry on construction and self-unregisters on destruction,
 * so the acoustic simulation thread can push per-frame parameters to it.
 */
class AUD_API PhononReader : public EffectReader
{
private:
  PhononDSPProcessor *m_dsp;
  std::vector<sample_t> m_temp_in;
  /** Owning speaker (Object *), or nullptr if this reader has no key. */
  const void *m_speaker_key;

public:
  /**
   * Construct a PhononReader wrapping \a reader.
   * \param speaker_key  Opaque speaker pointer used for the global reader registry.
   *                     Pass nullptr if this reader is not owned by a scene speaker.
   */
  PhononReader(std::shared_ptr<IReader> reader, const void *speaker_key = nullptr);
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
