/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#ifdef WITH_STEAM_AUDIO

#include "ISound.h"
#include "phonon_reader.h"

#include <map>
#include <memory>
#include <mutex>

AUD_NAMESPACE_BEGIN

/**
 * ISound wrapper that routes audio through Steam Audio's direct effect pipeline
 * (occlusion, transmission, distance attenuation, HRTF binaural spatialization).
 *
 * Each call to createReader() produces a PhononReader that processes audio in real time.
 * The most recently created reader per speaker key is registered globally so that the
 * acoustic simulation thread can push updated occlusion/transmission parameters to it
 * without rebuilding the reader chain.
 */
class AUD_API PhononSound : public ISound
{
private:
  PhononSound(const PhononSound &) = delete;
  PhononSound &operator=(const PhononSound &) = delete;

  /** Underlying raw sound source. */
  std::shared_ptr<ISound> m_sound;

  /**
   * Opaque key identifying the Blender speaker Object * that owns this sound.
   * Used to register the live reader in the global parameter map.
   */
  const void *m_speaker_key;

public:
  PhononSound(std::shared_ptr<ISound> sound, const void *speaker_key);
  virtual ~PhononSound() = default;

  /** Creates a PhononReader wrapping the underlying sound's reader. */
  virtual std::shared_ptr<IReader> createReader() override;
};

/* -------------------------------------------------------------------- */
/** \name Global active-reader registry (speaker_key → PhononReader *)
 *
 * The acoustic simulation (phonon_simulator_source_simulate) writes occlusion /
 * transmission / direction results here after each frame.  PhononReader::read()
 * calls phonon_dsp_process which reads the per-frame parameters that were last
 * set via phonon_dsp_set_parameters.
 * \{ */

/**
 * Register a live PhononReader for a speaker.  Called by PhononSound::createReader().
 * Replaces any previously registered reader for the same key.
 */
void phonon_sound_register_reader(const void *speaker_key, PhononReader *reader);

/**
 * Unregister a PhononReader (called from PhononReader destructor).
 */
void phonon_sound_unregister_reader(const void *speaker_key, PhononReader *reader);

/**
 * Push acoustic simulation results to the live PhononReader for a speaker.
 * Called from BKE_acoustics_scene_update_audio after each simulate pass.
 */
void phonon_sound_update_reader_params(const void *speaker_key,
                                       float occlusion,
                                       const float transmission[3],
                                       float distance_attenuation,
                                       const float rel_direction[3]);

/** \} */

AUD_NAMESPACE_END

#endif /* WITH_STEAM_AUDIO */
