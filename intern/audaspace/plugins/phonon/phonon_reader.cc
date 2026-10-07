/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "phonon_reader.h"
#include <cstring>

AUD_NAMESPACE_BEGIN

PhononReader::PhononReader(std::shared_ptr<IReader> reader)
    : EffectReader(reader), m_dsp(nullptr)
{
  Specs specs = m_reader->getSpecs();
  int in_ch = (specs.channels == CHANNELS_MONO) ? 1 : 2;
  m_dsp = phonon_dsp_create(specs.rate, 512, in_ch);
}

PhononReader::~PhononReader()
{
  if (m_dsp) {
    phonon_dsp_destroy(m_dsp);
    m_dsp = nullptr;
  }
}

Specs PhononReader::getSpecs() const
{
  Specs specs = m_reader->getSpecs();
  specs.channels = CHANNELS_STEREO; /* Output of binaural effect is stereo */
  return specs;
}

void PhononReader::read(int &length, bool &eos, sample_t *buffer)
{
  if (!m_dsp) {
    m_reader->read(length, eos, buffer);
    return;
  }

  Specs in_specs = m_reader->getSpecs();
  int in_ch = (in_specs.channels == CHANNELS_MONO) ? 1 : 2;

  m_temp_in.resize(length * in_ch);
  m_reader->read(length, eos, m_temp_in.data());

  if (length <= 0) {
    return;
  }

  phonon_dsp_process(m_dsp, m_temp_in.data(), in_ch, buffer, length);
}

void PhononReader::updateParameters(float directivity,
                                    float distance_attenuation,
                                    float occlusion,
                                    const float transmission[3],
                                    const float rel_direction[3])
{
  if (m_dsp) {
    phonon_dsp_set_parameters(m_dsp, directivity, distance_attenuation, occlusion, transmission, rel_direction);
  }
}

AUD_NAMESPACE_END

#endif /* WITH_STEAM_AUDIO */
