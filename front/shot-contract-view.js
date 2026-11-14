(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ShotContractView = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const MODE_LABELS = {
    text: "文本",
    first_frame: "首帧",
    first_last_frame: "首尾帧",
    reference: "参考",
  };

  const FEED_FIELDS = [
    { key: "title", label: "标题" },
    { key: "subject_definitions", label: "主体定义 subject_definitions" },
    { key: "detailed_description", label: "细节描述 detailed_description" },
    { key: "overall_soundscape", label: "声景 overall_soundscape" },
    { key: "non_diegetic_music", label: "音乐 non_diegetic_music" },
  ];

  function resolvedOf(shot) {
    const resolved = shot && shot.video_mode_resolved;
    return resolved && typeof resolved === "object" ? resolved : {};
  }

  function feedOf(shot) {
    const feed = shot && shot.feed;
    return feed && typeof feed === "object" ? feed : null;
  }

  function modeLabel(value) {
    const key = String(value || "");
    return Object.prototype.hasOwnProperty.call(MODE_LABELS, key) ? MODE_LABELS[key] : "";
  }

  function modeBadge(shot) {
    const resolved = resolvedOf(shot);
    const label = modeLabel(resolved.value);
    if (!label) return { text: "空", empty: true };
    return { text: resolved.inferred ? label + "·推断" : label, empty: false };
  }

  function tailBadge(shot) {
    const value = shot && shot.end_image != null ? String(shot.end_image).trim() : "";
    if (value) return { text: "尾帧有", empty: false };
    return { text: "尾帧空", empty: true };
  }

  function submitBadge(shot) {
    const value = String(resolvedOf(shot).value || "").trim();
    if (!value) return { text: "实际提交 · 空", warn: true, empty: true };
    return { text: "实际提交 · " + value, warn: false, empty: false };
  }

  function writtenBadge(shot) {
    const resolved = resolvedOf(shot);
    if (resolved.inferred) {
      return { text: "写入 · 空（提交时按规则推断）", warn: true, empty: true };
    }
    const written = resolved.written != null ? String(resolved.written).trim() : "";
    if (!written) return { text: "写入 · 空", warn: true, empty: true };
    return { text: "写入 · " + written, warn: false, empty: false };
  }

  function imagesSourceText(source) {
    if (!source || typeof source !== "object") return "空";
    const value = String(source.value || "");
    if (value === "unit_match") return "投喂单元匹配";
    if (value === "storyboard_index_fallback") {
      if (typeof source.index === "number" && isFinite(source.index)) {
        return "分镜序号兜底 · " + source.index;
      }
      return "分镜序号兜底";
    }
    if (value === "storyboard") return "分镜模板";
    if (!value) return "空";
    return value;
  }

  function previousShotId(shot, shots) {
    const list = Array.isArray(shots) ? shots : [];
    const index = list.findIndex((item) => item && shot && item.id === shot.id);
    if (index <= 0) return "";
    const previous = list[index - 1];
    return previous && previous.id ? String(previous.id) : "";
  }

  function firstFrameText(shot) {
    const source = shot && shot.first_frame_source != null ? String(shot.first_frame_source).trim() : "";
    if (!source || source === "none") return "无";
    if (source === "previous_tail") return "上一镜真实尾帧";
    return source;
  }

  function referenceNote(shot, shots) {
    const refs = shot && Array.isArray(shot.reference) ? shot.reference : [];
    const mode = String(resolvedOf(shot).value || "");
    const tail = refs.find((item) => item && item.source === "previous_tail");
    const source = shot && shot.first_frame_source != null ? String(shot.first_frame_source).trim() : "";
    const prev = previousShotId(shot, shots);
    const prevLabel = prev || "上一镜";
    if (source === "previous_tail" || (shot && shot.first_frame && shot.first_frame.source === "previous_tail")) {
      const frame = shot.first_frame || {};
      const pending = !!(frame.pending || frame.available === false || !frame.url);
      let text = "首帧用上一镜 " + prevLabel + " 的真实尾帧，不进参考列表。";
      if (pending) text += "上镜尾帧还没生成。";
      return text;
    }
    if (!refs.length) {
      const label = modeLabel(mode);
      if (label && mode !== "reference") {
        return "按提交规则预测：" + label + "模式不会附带 Ref2VA 参考列表。";
      }
      return "按提交规则预测的参考列表。这一镜没有参考图。";
    }
    if (tail && mode === "reference") {
      return "参考模式只提交这一镜绑定的资产图，不会把上一镜尾帧当作参考图。";
    }
    return "按提交规则预测的参考列表。这一镜没有追加上镜尾帧。";
  }

  function feedHeading(shot) {
    const feed = feedOf(shot);
    if (!feed) return "投喂稿";
    const unit = String(feed.unit || "").trim();
    if (unit) return "投喂稿 · 封装单元 " + unit;
    const title = String(feed.title || "").trim();
    if (title) return "投喂稿 · " + title;
    const heading = String(feed.heading || "").replace(/^#+\s*/, "").trim();
    if (heading) return "投喂稿 · " + heading;
    return "投喂稿";
  }

  function feedRows(shot) {
    const feed = feedOf(shot) || {};
    return FEED_FIELDS.map((field) => {
      const raw = feed[field.key] != null ? String(feed[field.key]) : "";
      const text = raw.trim();
      return {
        key: field.key,
        label: field.label,
        text: text || "空",
        empty: !text,
      };
    });
  }

  function durationText(shot) {
    if (!shot || shot.duration == null || shot.duration === "") return "空";
    return String(shot.duration) + " 秒";
  }

  function contractNote() {
    return "封装通过后实际用于出片的字段，不是分镜模板。空值会明确标成「空」，不会被隐藏。";
  }

  function contractSig(shot) {
    if (!shot || typeof shot !== "object") return "";
    const resolved = resolvedOf(shot);
    return JSON.stringify({
      duration: shot.duration == null ? "" : shot.duration,
      images: shot.images || [],
      end_image: shot.end_image || "",
      images_source: shot.images_source || null,
      video_mode_resolved: {
        value: resolved.value || "",
        inferred: !!resolved.inferred,
        written: resolved.written != null ? String(resolved.written) : "",
      },
      first_frame_source: shot.first_frame_source || "",
      reference: Array.isArray(shot.reference) ? shot.reference : [],
      feed: feedOf(shot),
    });
  }

  return {
    modeBadge: modeBadge,
    tailBadge: tailBadge,
    submitBadge: submitBadge,
    writtenBadge: writtenBadge,
    imagesSourceText: imagesSourceText,
    firstFrameText: firstFrameText,
    referenceNote: referenceNote,
    feedHeading: feedHeading,
    feedRows: feedRows,
    durationText: durationText,
    contractNote: contractNote,
    contractSig: contractSig,
  };
});
