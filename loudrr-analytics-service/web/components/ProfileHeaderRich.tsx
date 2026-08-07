import Image from "next/image";
import { CalendarBlank, Globe, MapPin, PencilSimple, SealCheck, Users } from "@phosphor-icons/react/dist/ssr";
import { avatar, type Profile } from "@/lib/mock";
import { fmtCompact } from "@/lib/score";

// Researched-profile header (TwitterScore-style): avatar, identity, bio, location, join year,
// rename count, follower count. Server-safe.
export function ProfileHeaderRich({ profile }: { profile: Profile }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
      <Image
        src={avatar(profile.handle)}
        alt={profile.handle}
        width={72}
        height={72}
        unoptimized
        className="h-[72px] w-[72px] shrink-0 rounded-2xl border border-white/10 bg-ink-700 object-cover"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate font-display text-2xl font-bold tracking-tightest">{profile.name}</h1>
          {profile.verified && <SealCheck size={18} weight="fill" className="shrink-0 text-flare" />}
        </div>
        <a
          href={`https://x.com/${profile.handle}`}
          target="_blank"
          rel="noreferrer"
          className="font-mono text-sm text-bone-500 transition-colors hover:text-flare"
        >
          @{profile.handle}
        </a>
        {profile.meta.bio && <p className="mt-2 max-w-lg text-sm leading-relaxed text-bone-300">{profile.meta.bio}</p>}
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 font-mono text-xs text-bone-500">
          <Meta icon={<Users size={13} weight="fill" />}>{fmtCompact(profile.followers)} followers</Meta>
          <Meta icon={<MapPin size={13} weight="fill" />}>{profile.meta.location}</Meta>
          <Meta icon={<CalendarBlank size={13} weight="fill" />}>joined {profile.meta.joinYear}</Meta>
          {profile.meta.renamedCount > 0 && (
            <Meta icon={<PencilSimple size={13} weight="fill" />}>renamed {profile.meta.renamedCount}×</Meta>
          )}
          {profile.meta.website && (
            <Meta icon={<Globe size={13} weight="fill" />}>
              <span className="text-flare-400">{profile.meta.website}</span>
            </Meta>
          )}
        </div>
      </div>
    </div>
  );
}

function Meta({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {icon}
      {children}
    </span>
  );
}
