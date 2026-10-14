/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "phonon_sound.h"
#include "phonon_reader.h"

#include <map>
#include <mutex>

AUD_NAMESPACE_BEGIN

/* -------------------------------------------------------------------- */
/** \name Global registry: speaker_key → live PhononReader *
 * \{ */

static std::mutex g_reader_map_mutex;
/* Maps speaker object pointer to the most recently created PhononReader for it.
 * Weak reference — reader manages its own lifetime and unregisters on destruction. */
static std::map<const void *, PhononReader *> g_speaker_readers;

void phonon_sound_register_reader(const void *speaker_key, PhononReader *reader)
{
  if (!speaker_key || !reader) {
    return;
  }
  std::lock_guard<std::mutex> lock(g_reader_map_mutex);
  g_speaker_readers[speaker_key] = reader;
}

void phonon_sound_unregister_reader(const void *speaker_key, PhononReader *reader)
{
  if (!speaker_key) {
    return;
  }
  std::lock_guard<std::mutex> lock(g_reader_map_mutex);
  auto it = g_speaker_readers.find(speaker_key);
  if (it != g_speaker_readers.end() && it->second == reader) {
    g_speaker_readers.erase(it);
  }
}

void phonon_sound_update_reader_params(const void *speaker_key,
                                       float occlusion,
                                       const float transmission[3],
                                       float distance_attenuation,
                                       const float rel_direction[3])
{
  std::lock_guard<std::mutex> lock(g_reader_map_mutex);
  auto it = g_speaker_readers.find(speaker_key);
  if (it != g_speaker_readers.end() && it->second) {
    it->second->updateParameters(1.0f, distance_attenuation, occlusion, transmission, rel_direction);
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name PhononSound
 * \{ */

PhononSound::PhononSound(std::shared_ptr<ISound> sound, const void *speaker_key)
    : m_sound(sound), m_speaker_key(speaker_key)
{
}

std::shared_ptr<IReader> PhononSound::createReader()
{
  if (!m_sound) {
    return nullptr;
  }
  std::shared_ptr<IReader> inner = m_sound->createReader();
  if (!inner) {
    return nullptr;
  }
  auto phonon_reader = std::make_shared<PhononReader>(inner);
  /* Register this reader so acoustic simulation can push params to it. */
  phonon_sound_register_reader(m_speaker_key, phonon_reader.get());
  return phonon_reader;
}

/** \} */

AUD_NAMESPACE_END

#endif /* WITH_STEAM_AUDIO */
